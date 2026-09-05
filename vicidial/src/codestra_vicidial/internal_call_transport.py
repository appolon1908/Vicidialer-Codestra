"""Private loopback AMI transport for the bounded internal-call service only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import time
from pathlib import Path
from uuid import uuid4

from .ami_gateway import AMIClient, AMIStreamParser, parse_ami_block


class CallDenied(PermissionError):
    pass


class CallUnavailable(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_protected_json(path):
    if not path.is_absolute():
        raise CallDenied("absolute protected path required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        service_owned = info.st_uid == os.geteuid() and not info.st_mode & 0o077
        root_group_readable = (
            info.st_uid == 0
            and info.st_gid == os.getegid()
            and info.st_mode & 0o040
            and not info.st_mode & 0o037
        )
        if not stat.S_ISREG(info.st_mode) or not (
            service_owned or root_group_readable
        ):
            raise CallDenied("configuration must be root-owned and protected")
        raw = stream.read(65537)
        if len(raw) > 65536:
            raise CallDenied("configuration too large")

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise CallDenied("duplicate configuration key")
                value[key] = item
            return value

        return json.loads(raw, object_pairs_hook=unique)


class SocketAmiTransport:
    def __init__(self, credential_file: Path):
        self.credential_file = credential_file

    def _request(self, action: str, fields: dict, complete: str | None = None) -> list:
        if action not in {"ShowDialPlan", "Originate", "Status", "Hangup"}:
            raise CallDenied("AMI action outside internal call contract")
        config = load_protected_json(self.credential_file)
        if config.get("host", "127.0.0.1") not in {"127.0.0.1", "localhost"}:
            raise CallDenied("AMI must remain loopback-only")
        port = config.get("port", 5038)
        if type(port) is not int or not 1 <= port <= 65535:
            raise CallDenied("invalid loopback AMI port")

        def encode(values):
            if any(
                not isinstance(v, str) or len(v) > 256 or any(ord(c) < 32 for c in v)
                for v in values.values()
            ):
                raise CallDenied("invalid AMI header")
            return (
                "\r\n".join(f"{k}: {v}" for k, v in values.items()) + "\r\n\r\n"
            ).encode()

        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.settimeout(3)
            if not AMIClient._read_line(connection).startswith(
                b"Asterisk Call Manager/"
            ):
                raise CallUnavailable("invalid AMI greeting")
            connection.sendall(
                encode(
                    {
                        "Action": "Login",
                        "Username": config["username"],
                        "Secret": config["secret"],
                        "Events": "off",
                    }
                )
            )
            raw, pending = AMIClient._read_block(connection)
            if parse_ami_block(raw).response.lower() != "success":
                raise CallUnavailable("AMI authentication rejected")
            action_id = "internal-" + str(uuid4())
            connection.sendall(
                encode({**fields, "Action": action, "ActionID": action_id})
            )
            parser, rows, accepted = AMIStreamParser(), [], False
            deadline, size = time.monotonic() + 5, 0
            while time.monotonic() < deadline:
                chunk = pending or connection.recv(65536)
                pending = b""
                size += len(chunk)
                if not chunk or size > 1024 * 1024:
                    raise CallUnavailable("AMI response incomplete or excessive")
                for message in parser.feed(chunk):
                    if message.get("ActionID") != action_id:
                        continue
                    if message.response:
                        if message.response.lower() != "success":
                            raise CallUnavailable("AMI action rejected")
                        accepted = True
                        if complete is None:
                            return []
                    elif message.event == complete and accepted:
                        return rows
                    elif message.event:
                        rows.append(message)
                        if len(rows) > 100:
                            raise CallUnavailable(
                                "AMI result exceeds bounded route/channel limit"
                            )
            raise CallUnavailable("AMI response deadline exceeded")

    def verify_route(self, policy):
        rows = self._request(
            "ShowDialPlan",
            {"Context": policy.dialplan_context, "Extension": "6000"},
            "ShowDialPlanComplete",
        )
        route = []
        for row in rows:
            if (
                row.event != "ListDialplan"
                or row.get("Context") != policy.dialplan_context
                or row.get("Extension") != "6000"
                or row.get("Switch")
                or row.get("IncludeContext")
                or row.get("IgnorePattern")
                or row.get("AppData") != ""
            ):
                raise CallDenied("route contains unapproved dialplan resolution")
            route.append(
                {
                    k: row.get(k)
                    for k in (
                        "Context",
                        "Extension",
                        "Priority",
                        "Application",
                        "AppData",
                    )
                }
            )
        route.sort(key=lambda r: int(r["Priority"]) if r["Priority"].isdigit() else -1)
        if (
            [r["Priority"] for r in route] != ["1", "2", "3"]
            or [r["Application"] for r in route] != ["Answer", "Echo", "Hangup"]
            or digest(route) != policy.route_sha256
        ):
            raise CallDenied(
                "live route 6000 differs from the approved isolated echo route"
            )

    def action(self, fields):
        values = dict(fields)
        action = values.pop("Action")
        if action == "Originate":
            self._request(action, values)
        elif action == "Hangup":
            uniqueid = values.pop("ExpectedUniqueid", "")
            channel = values.get("Channel", "")
            if not re.fullmatch(r"codestra-[a-f0-9]{32}", uniqueid):
                raise CallDenied("invalid stored channel identity")
            if not re.fullmatch(r"PJSIP/6901-[A-Fa-f0-9]+", channel):
                raise CallDenied("invalid resolved channel name")
            rows = self._request("Status", {"Channel": channel}, "StatusComplete")
            if (
                len(rows) != 1
                or rows[0].event != "Status"
                or rows[0].get("Uniqueid") != uniqueid
                or rows[0].get("Channel") != channel
            ):
                raise CallDenied("hangup channel is not the originated appolon leg")
            self._request(action, values)
        else:
            raise CallDenied("action outside internal call contract")
        return {"Response": "Success"}
