from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol

from .models import AgentSpec, CallbackSpec, CampaignSpec, LeadSpec


class CollisionError(ValueError):
    pass


class MalformedCampaignMembership(CollisionError):
    pass


def normalize_allowed_campaigns(value: str) -> list[str]:
    """Strictly parse VICIdial's `` CAMPAIGN OTHER -`` representation."""
    if not isinstance(value, str):
        raise MalformedCampaignMembership("allowed_campaigns must be text")
    stripped = value.strip()
    if stripped == "-ALL-CAMPAIGNS-":
        raise CollisionError("unrestricted user group cannot be adopted")
    if not stripped.endswith("-"):
        raise MalformedCampaignMembership("allowed_campaigns missing terminator")
    body = stripped[:-1].strip()
    if not body:
        return []
    tokens = body.split()
    if any(not __import__("re").fullmatch(r"[A-Z0-9_]{2,8}", token)
           for token in tokens):
        raise MalformedCampaignMembership(
            "allowed_campaigns contains malformed campaign")
    if len(tokens) != len(set(tokens)):
        raise MalformedCampaignMembership(
            "allowed_campaigns contains duplicate campaign")
    return tokens


def format_allowed_campaigns(campaigns: list[str]) -> str:
    if len(campaigns) != len(set(campaigns)):
        raise MalformedCampaignMembership("duplicate campaign")
    return (" " + " ".join(campaigns) if campaigns else "") + " -"


def additive_campaign_membership(current: str, desired: str) -> tuple[str, bool]:
    campaigns = normalize_allowed_campaigns(current)
    if desired in campaigns:
        return format_allowed_campaigns(campaigns), False
    return format_allowed_campaigns([*campaigns, desired]), True


class Repository(Protocol):
    def campaign(self, actual_id: str) -> dict | None: ...
    def campaign_resources(self, spec: CampaignSpec) -> dict: ...
    def provision_campaign_disabled(self, spec: CampaignSpec) -> list[dict]: ...
    def disable_campaign_resources(self, spec: CampaignSpec) -> None: ...
    def disable_campaign(self, actual_id: str) -> None: ...
    def agent(self, user_id: str) -> dict | None: ...
    def sync_agent_disabled(self, spec: AgentSpec) -> dict: ...
    def disable_agent(self, user_id: str) -> dict: ...
    def lead_by_external_id(self, external_id: str) -> dict | None: ...
    def publish_lead(self, spec: LeadSpec) -> dict: ...
    def callback(self, callback_id: int) -> dict | None: ...
    def callback_by_external_id(self, external_id: str) -> dict | None: ...
    def create_callback(self, spec: CallbackSpec) -> dict: ...
    def campaign_owner(self, actual_id: str) -> str | None: ...
    def transfer_target(self, code: str, require_active: bool = False) -> dict | None: ...
    def inventory(self) -> dict: ...


def callback_payload(spec: CallbackSpec) -> dict:
    return spec.model_dump(mode="json")


def callback_payload_hash(spec: CallbackSpec) -> str:
    return hashlib.sha256(json.dumps(
        callback_payload(spec), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


class MemoryRepository:
    def __init__(self):
        self.campaigns: dict[str, dict] = {}
        self.lists: dict[int, dict] = {}
        self.groups: dict[str, dict] = {}
        self.scripts: dict[str, dict] = {}
        self.dispositions: dict[tuple[str, str], dict] = {}
        self.agents: dict[str, dict] = {}
        self.leads: dict[int, dict] = {}
        self.callbacks: dict[int, dict] = {}

    @staticmethod
    def _adopt(store: dict, key: Any, desired: dict, compatible: tuple[str, ...]) -> str:
        current = store.get(key)
        if current is None:
            store[key] = deepcopy(desired)
            return "created"
        if any(current.get(field) != desired.get(field) for field in compatible):
            raise CollisionError(f"incompatible collision: {key}")
        return "adopted"

    def campaign(self, actual_id: str) -> dict | None:
        item = self.campaigns.get(actual_id)
        if item is None:
            return None
        result = deepcopy(item)
        result["lists"] = [deepcopy(row) for row in self.lists.values()
                           if row["campaign_id"] == actual_id]
        result["dispositions"] = [deepcopy(row) for (campaign, _), row in self.dispositions.items()
                                  if campaign == actual_id]
        return result

    def campaign_owner(self, actual_id: str) -> str | None:
        row = self.campaigns.get(actual_id)
        return str(row["canonical_id"]) if row and row.get("canonical_id") else None

    def transfer_target(self, code: str, require_active: bool = False) -> dict | None:
        rows = [
            deepcopy(row) for (kind, group_code), row in self.groups.items()
            if group_code == code and kind in {"inbound", "closer"}
        ]
        if len(rows) != 1:
            return None
        row = rows[0]
        if require_active and row.get("active") != "Y":
            return None
        return row

    def campaign_resources(self, spec: CampaignSpec) -> dict:
        return {
            "campaign": self.campaign(spec.actual_id),
            "groups": [
                deepcopy(self.groups[(item.kind, item.code)])
                for item in spec.groups
                if (item.kind, item.code) in self.groups
            ],
            "scripts": [
                deepcopy(self.scripts[item.code])
                for item in spec.scripts
                if item.code in self.scripts
            ],
        }

    def provision_campaign_disabled(self, spec: CampaignSpec) -> list[dict]:
        # Preflight every possible collision before the first mutation. This is
        # the compensating safety boundary used by both retry and rollback tests.
        campaign_desired = {
            "campaign_id": spec.actual_id, "canonical_id": spec.canonical_id,
            "campaign_name": spec.name, "active": "N", "dial_method": spec.dial_method,
            "local_call_time": spec.call_time,
        }
        checks = [
            (self.campaigns, spec.actual_id, campaign_desired,
             ("canonical_id", "campaign_name", "dial_method", "local_call_time")),
        ]
        checks.extend(
            (self.lists, item.list_id,
             {"list_name": item.code, "campaign_id": spec.actual_id, "active": "N"},
             ("list_name", "campaign_id", "active"))
            for item in spec.lists
        )
        checks.extend(
            (self.groups, (item.kind, item.code),
             {"name": item.name, "kind": item.kind, "active": "N"},
             ("name", "kind", "active"))
            for item in spec.groups
        )
        checks.extend(
            (self.scripts, item.code,
             {"script_name": item.name, "script_text": item.text,
              "version": item.version, "active": "N"},
             ("script_name", "script_text", "version", "active"))
            for item in spec.scripts
        )
        checks.extend(
            (self.dispositions, (spec.actual_id, item.code),
             {"status_name": item.name, "selectable": "Y" if item.selectable else "N",
              "human_answered": "Y" if item.human_answered else "N",
              "category": item.category},
             ("status_name", "selectable", "human_answered", "category"))
            for item in spec.dispositions
        )
        for store, key, desired, fields in checks:
            current = store.get(key)
            if current and any(current.get(field) != desired.get(field) for field in fields):
                raise CollisionError(f"incompatible collision: {key}")
        for item in spec.groups:
            if item.kind == "user":
                if not item.code.startswith(spec.business_unit + "_"):
                    raise CollisionError(f"incompatible ownership: {item.code}")
                current = self.groups.get((item.kind, item.code))
                if current:
                    additive_campaign_membership(
                        current.get("allowed_campaigns"), spec.actual_id)
        results = []
        campaign = {
            "campaign_id": spec.actual_id, "canonical_id": spec.canonical_id,
            "campaign_name": spec.name, "active": "N", "dial_method": spec.dial_method,
            "local_call_time": spec.call_time, "campaign_recording": spec.recording_policy,
            "scheduled_callbacks": "Y" if spec.callback_enabled else "N",
        }
        status = self._adopt(self.campaigns, spec.actual_id, campaign,
                             ("canonical_id", "campaign_name", "dial_method", "local_call_time"))
        results.append({"type": "campaign", "id": spec.actual_id, "result": status})
        for item in spec.lists:
            desired = {"list_id": item.list_id, "list_name": item.code,
                       "campaign_id": spec.actual_id, "active": "N"}
            status = self._adopt(self.lists, item.list_id, desired,
                                 ("list_name", "campaign_id", "active"))
            results.append({"type": "list", "id": item.list_id, "result": status})
        for item in spec.groups:
            desired = {"code": item.code, "name": item.name, "kind": item.kind, "active": "N"}
            if item.kind == "user":
                if not item.code.startswith(spec.business_unit + "_"):
                    raise CollisionError(f"incompatible ownership: {item.code}")
                current = self.groups.get((item.kind, item.code))
                if current is None:
                    desired["allowed_campaigns"] = format_allowed_campaigns(
                        [spec.actual_id])
                    self.groups[(item.kind, item.code)] = deepcopy(desired)
                    status = "created"
                else:
                    if any(current.get(field) != desired.get(field)
                           for field in ("name", "kind", "active")):
                        raise CollisionError(
                            f"incompatible collision: {item.code}")
                    normalized, added = additive_campaign_membership(
                        current.get("allowed_campaigns"), spec.actual_id)
                    current["allowed_campaigns"] = normalized
                    status = "updated" if added else "adopted"
            else:
                status = self._adopt(
                    self.groups, (item.kind, item.code), desired,
                    ("name", "kind", "active"))
            results.append({"type": item.kind + "_group", "id": item.code, "result": status})
        for item in spec.scripts:
            desired = {"script_id": item.code, "script_name": item.name, "script_text": item.text,
                       "version": item.version, "active": "N"}
            status = self._adopt(self.scripts, item.code, desired,
                                 ("script_name", "script_text", "version", "active"))
            results.append({"type": "script", "id": item.code, "result": status})
        for item in spec.dispositions:
            desired = {"campaign_id": spec.actual_id, "status": item.code,
                       "status_name": item.name, "selectable": "Y" if item.selectable else "N",
                       "human_answered": "Y" if item.human_answered else "N",
                       "category": item.category}
            status = self._adopt(self.dispositions, (spec.actual_id, item.code), desired,
                                 ("status_name", "selectable", "human_answered", "category"))
            results.append({"type": "disposition", "id": item.code, "result": status})
        return results

    def disable_campaign(self, actual_id: str) -> None:
        if actual_id not in self.campaigns:
            raise ValueError("campaign missing")
        self.campaigns[actual_id]["active"] = "N"
        for item in self.lists.values():
            if item["campaign_id"] == actual_id:
                item["active"] = "N"

    def disable_campaign_resources(self, spec: CampaignSpec) -> None:
        if spec.actual_id in self.campaigns:
            self.disable_campaign(spec.actual_id)
        for item in spec.groups:
            row = self.groups.get((item.kind, item.code))
            if row is not None and item.kind != "user":
                row["active"] = "N"
        for item in spec.scripts:
            row = self.scripts.get(item.code)
            if row is not None:
                row["active"] = "N"

    def agent(self, user_id: str) -> dict | None:
        return deepcopy(self.agents.get(user_id))

    def sync_agent_disabled(self, spec: AgentSpec) -> dict:
        current = self.agents.get(spec.user_id)
        desired = spec.model_dump(mode="json") | {"active": False}
        if current and current.get("full_name") != spec.full_name:
            raise CollisionError(f"incompatible collision: {spec.user_id}")
        self.agents[spec.user_id] = desired
        return deepcopy(desired)

    def disable_agent(self, user_id: str) -> dict:
        if user_id not in self.agents:
            raise ValueError("agent missing")
        self.agents[user_id]["active"] = False
        return deepcopy(self.agents[user_id])

    def lead_by_external_id(self, external_id: str) -> dict | None:
        return next((deepcopy(row) for row in self.leads.values()
                     if row["external_id"] == external_id), None)

    def publish_lead(self, spec: LeadSpec) -> dict:
        prior = self.lead_by_external_id(spec.external_id)
        if prior:
            desired = spec.model_dump(mode="json")
            if any(prior.get(key) != value for key, value in desired.items()):
                raise CollisionError("external lead id collision")
            return prior
        lead_id = max(self.leads, default=0) + 1
        result = spec.model_dump(mode="json") | {"lead_id": lead_id}
        self.leads[lead_id] = result
        return deepcopy(result)

    def callback(self, callback_id: int) -> dict | None:
        return deepcopy(self.callbacks.get(callback_id))

    def callback_by_external_id(self, external_id: str) -> dict | None:
        return next((deepcopy(row) for row in self.callbacks.values()
                     if row["external_id"] == external_id), None)

    def create_callback(self, spec: CallbackSpec) -> dict:
        prior = self.callback_by_external_id(spec.external_id)
        if prior:
            if any(str(prior.get(field)) != str(value)
                   for field, value in callback_payload(spec).items()):
                raise CollisionError("callback external id collision")
            return prior
        callback_id = max(self.callbacks, default=0) + 1
        result = spec.model_dump(mode="json") | {"callback_id": callback_id, "status": "ACTIVE"}
        self.callbacks[callback_id] = result
        return deepcopy(result)

    def inventory(self) -> dict:
        return {
            "campaigns": len(self.campaigns), "lists": len(self.lists),
            "groups": len(self.groups), "scripts": len(self.scripts),
            "dispositions": len(self.dispositions), "agents": len(self.agents),
            "leads": len(self.leads), "callbacks": len(self.callbacks),
        }


class MySQLRepository:
    """Narrow, parameterized VICIdial repository. No caller-supplied SQL or identifiers."""

    def __init__(self, connection):
        self.db = connection

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.db.cursor() as cursor:
            cursor.execute(sql, args)
            row = cursor.fetchone()
            return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.db.cursor() as cursor:
            cursor.execute(sql, args)
            return [dict(row) for row in cursor.fetchall()]

    def _execute(self, sql: str, args: tuple = ()) -> int:
        with self.db.cursor() as cursor:
            return cursor.execute(sql, args)

    def campaign(self, actual_id: str) -> dict | None:
        row = self._one(
            "SELECT campaign_id,campaign_name,active,dial_method,local_call_time,"
            "campaign_recording,scheduled_callbacks,campaign_description "
            "FROM vicidial_campaigns WHERE campaign_id=%s", (actual_id,))
        if not row:
            return None
        row["lists"] = self._all(
            "SELECT list_id,list_name,campaign_id,active FROM vicidial_lists WHERE campaign_id=%s",
            (actual_id,))
        row["dispositions"] = self._all(
            "SELECT status,status_name,selectable,human_answered,category "
            "FROM vicidial_campaign_statuses WHERE campaign_id=%s", (actual_id,))
        return row

    def campaign_owner(self, actual_id: str) -> str | None:
        row = self._one(
            "SELECT campaign_description FROM vicidial_campaigns "
            "WHERE campaign_id=%s", (actual_id,))
        marker = str(row.get("campaign_description", "")) if row else ""
        return marker.removeprefix("CODESTRA:") if marker.startswith(
            "CODESTRA:") else None

    def transfer_target(self, code: str, require_active: bool = False) -> dict | None:
        row = self._one(
            "SELECT group_id AS code,group_name AS name,active "
            "FROM vicidial_inbound_groups WHERE group_id=%s", (code,))
        if row is None or (require_active and row.get("active") != "Y"):
            return None
        return row

    def campaign_resources(self, spec: CampaignSpec) -> dict:
        groups = []
        for item in spec.groups:
            if item.kind == "user":
                row = self._one(
                    "SELECT user_group AS code,group_name AS name,allowed_campaigns "
                    "FROM vicidial_user_groups WHERE user_group=%s", (item.code,))
                if row:
                    row["kind"] = "user"
                    row["active"] = "N"
            else:
                row = self._one(
                    "SELECT group_id AS code,group_name AS name,active "
                    "FROM vicidial_inbound_groups WHERE group_id=%s", (item.code,))
                if row:
                    row["kind"] = item.kind
            if row:
                groups.append(row)
        scripts = []
        for item in spec.scripts:
            row = self._one(
                "SELECT script_id,script_name,script_comments,script_text,active "
                "FROM vicidial_scripts WHERE script_id=%s", (item.code,))
            if row:
                scripts.append(row)
        return {
            "campaign": self.campaign(spec.actual_id),
            "groups": groups,
            "scripts": scripts,
        }

    @staticmethod
    def _check(current: dict | None, desired: dict, fields: tuple[str, ...], target: str) -> bool:
        if current is None:
            return False
        if any(str(current.get(field)) != str(desired.get(field)) for field in fields):
            raise CollisionError(f"incompatible collision: {target}")
        return True

    def provision_campaign_disabled(self, spec: CampaignSpec) -> list[dict]:
        # Tables are MyISAM on this host. Each creation is disabled immediately;
        # on a later failure, created campaign/list/script/ingroup rows are retained
        # disabled as evidence and safe retry targets rather than deleted.
        results: list[dict] = []
        description = f"CODESTRA:{spec.canonical_id}"
        desired_campaign = {
            "campaign_id": spec.actual_id, "campaign_name": spec.name, "active": "N",
            "dial_method": spec.dial_method, "local_call_time": spec.call_time,
            "campaign_recording": spec.recording_policy,
            "scheduled_callbacks": "Y" if spec.callback_enabled else "N",
            "campaign_description": description,
        }
        current = self._one(
            "SELECT campaign_id,campaign_name,active,dial_method,local_call_time,"
            "campaign_recording,scheduled_callbacks,campaign_description "
            "FROM vicidial_campaigns WHERE campaign_id=%s", (spec.actual_id,))
        adopted = self._check(current, desired_campaign,
                              ("campaign_name", "active", "dial_method", "local_call_time",
                               "campaign_recording", "scheduled_callbacks", "campaign_description"),
                              spec.actual_id)
        # Complete collision preflight before the first MyISAM write. MyISAM
        # cannot provide transactional rollback, so a late ownership collision
        # must never be discovered after earlier resources were inserted.
        for item in spec.lists:
            current_list = self._one(
                "SELECT list_id,list_name,campaign_id,active FROM vicidial_lists WHERE list_id=%s",
                (item.list_id,))
            self._check(
                current_list,
                {"list_name": item.code, "campaign_id": spec.actual_id, "active": "N"},
                ("list_name", "campaign_id", "active"), str(item.list_id),
            )
        for item in spec.groups:
            if item.kind == "user":
                if not item.code.startswith(spec.business_unit + "_"):
                    raise CollisionError(f"incompatible ownership: {item.code}")
                current_group = self._one(
                    "SELECT user_group AS code,group_name AS name,allowed_campaigns "
                    "FROM vicidial_user_groups "
                    "WHERE user_group=%s", (item.code,))
                self._check(current_group, {"name": item.name},
                            ("name",), item.code)
                if current_group:
                    additive_campaign_membership(
                        current_group.get("allowed_campaigns"), spec.actual_id)
            else:
                current_group = self._one(
                    "SELECT group_id AS code,group_name AS name,active "
                    "FROM vicidial_inbound_groups WHERE group_id=%s", (item.code,))
                self._check(
                    current_group, {"name": item.name, "active": "N"},
                    ("name", "active"), item.code,
                )
        for item in spec.scripts:
            current_script = self._one(
                "SELECT script_id,script_name,script_text,active FROM vicidial_scripts "
                "WHERE script_id=%s", (item.code,))
            self._check(
                current_script,
                {"script_name": item.name, "script_text": item.text, "active": "N"},
                ("script_name", "script_text", "active"), item.code,
            )
        for item in spec.dispositions:
            current_status = self._one(
                "SELECT status_name,selectable,human_answered,category "
                "FROM vicidial_campaign_statuses WHERE campaign_id=%s AND status=%s",
                (spec.actual_id, item.code))
            self._check(
                current_status,
                {"status_name": item.name,
                 "selectable": "Y" if item.selectable else "N",
                 "human_answered": "Y" if item.human_answered else "N",
                 "category": item.category},
                ("status_name", "selectable", "human_answered", "category"), item.code,
            )
        if not adopted:
            self._execute(
                "INSERT INTO vicidial_campaigns"
                "(campaign_id,campaign_name,active,dial_method,local_call_time,"
                "campaign_recording,scheduled_callbacks,campaign_description,hopper_level,auto_dial_level)"
                " VALUES(%s,%s,'N',%s,%s,%s,%s,%s,0,'0')",
                (spec.actual_id, spec.name, spec.dial_method, spec.call_time,
                 spec.recording_policy, desired_campaign["scheduled_callbacks"], description))
        results.append({"type": "campaign", "id": spec.actual_id,
                        "result": "adopted" if adopted else "created"})
        for item in spec.lists:
            desired = {"list_id": item.list_id, "list_name": item.code,
                       "campaign_id": spec.actual_id, "active": "N"}
            current = self._one(
                "SELECT list_id,list_name,campaign_id,active FROM vicidial_lists WHERE list_id=%s",
                (item.list_id,))
            adopted = self._check(current, desired, ("list_name", "campaign_id", "active"),
                                  str(item.list_id))
            if not adopted:
                self._execute(
                    "INSERT INTO vicidial_lists(list_id,list_name,campaign_id,active,list_description)"
                    " VALUES(%s,%s,%s,'N',%s)",
                    (item.list_id, item.code, spec.actual_id, description))
            results.append({"type": "list", "id": item.list_id,
                            "result": "adopted" if adopted else "created"})
        for item in spec.groups:
            if item.kind == "user":
                current = self._one(
                    "SELECT user_group AS code,group_name AS name,allowed_campaigns "
                    "FROM vicidial_user_groups "
                    "WHERE user_group=%s", (item.code,))
                adopted = self._check(
                    current,
                    {"code": item.code, "name": item.name},
                    ("name",), item.code,
                )
                if not adopted:
                    self._execute(
                        "INSERT INTO vicidial_user_groups(user_group,group_name,allowed_campaigns)"
                        " VALUES(%s,%s,%s)",
                        (item.code, item.name,
                         format_allowed_campaigns([spec.actual_id])))
                    group_result = "created"
                else:
                    normalized, added = additive_campaign_membership(
                        current["allowed_campaigns"], spec.actual_id)
                    if added:
                        self._execute(
                            "UPDATE vicidial_user_groups SET allowed_campaigns=%s "
                            "WHERE user_group=%s", (normalized, item.code))
                    group_result = "updated" if added else "adopted"
            else:
                current = self._one(
                    "SELECT group_id AS code,group_name AS name,active FROM vicidial_inbound_groups "
                    "WHERE group_id=%s", (item.code,))
                adopted = self._check(current, {"code": item.code, "name": item.name, "active": "N"},
                                      ("name", "active"), item.code)
                if not adopted:
                    self._execute(
                        "INSERT INTO vicidial_inbound_groups(group_id,group_name,active,"
                        "group_color,group_handling,call_time_id) VALUES(%s,%s,'N','white','PHONE','24hours')",
                        (item.code, item.name))
                group_result = "adopted" if adopted else "created"
            results.append({"type": item.kind + "_group", "id": item.code,
                            "result": group_result})
        for item in spec.scripts:
            current = self._one(
                "SELECT script_id,script_name,script_text,active FROM vicidial_scripts WHERE script_id=%s",
                (item.code,))
            desired = {"script_name": item.name, "script_text": item.text, "active": "N"}
            adopted = self._check(current, desired, ("script_name", "script_text", "active"), item.code)
            if not adopted:
                self._execute(
                    "INSERT INTO vicidial_scripts(script_id,script_name,script_comments,script_text,active)"
                    " VALUES(%s,%s,%s,%s,'N')",
                    (item.code, item.name, f"{description}:V{item.version}", item.text))
            results.append({"type": "script", "id": item.code,
                            "result": "adopted" if adopted else "created"})
        for item in spec.dispositions:
            current = self._one(
                "SELECT status_name,selectable,human_answered,category FROM vicidial_campaign_statuses "
                "WHERE campaign_id=%s AND status=%s", (spec.actual_id, item.code))
            desired = {"status_name": item.name, "selectable": "Y" if item.selectable else "N",
                       "human_answered": "Y" if item.human_answered else "N",
                       "category": item.category}
            adopted = self._check(current, desired,
                                  ("status_name", "selectable", "human_answered", "category"), item.code)
            if not adopted:
                self._execute(
                    "INSERT INTO vicidial_campaign_statuses"
                    "(campaign_id,status,status_name,selectable,human_answered,category)"
                    " VALUES(%s,%s,%s,%s,%s,%s)",
                    (spec.actual_id, item.code, item.name, desired["selectable"],
                     desired["human_answered"], item.category))
            results.append({"type": "disposition", "id": item.code,
                            "result": "adopted" if adopted else "created"})
        self.db.commit()
        return results

    def disable_campaign(self, actual_id: str) -> None:
        if not self.campaign(actual_id):
            raise ValueError("campaign missing")
        self._execute("UPDATE vicidial_campaigns SET active='N',auto_dial_level='0' WHERE campaign_id=%s",
                      (actual_id,))
        self._execute("UPDATE vicidial_lists SET active='N' WHERE campaign_id=%s", (actual_id,))
        self.db.commit()

    def disable_campaign_resources(self, spec: CampaignSpec) -> None:
        self._execute(
            "UPDATE vicidial_campaigns SET active='N',auto_dial_level='0' "
            "WHERE campaign_id=%s", (spec.actual_id,))
        self._execute(
            "UPDATE vicidial_lists SET active='N' WHERE campaign_id=%s",
            (spec.actual_id,))
        for item in spec.groups:
            if item.kind != "user":
                self._execute(
                    "UPDATE vicidial_inbound_groups SET active='N' "
                    "WHERE group_id=%s", (item.code,))
        for item in spec.scripts:
            self._execute(
                "UPDATE vicidial_scripts SET active='N' WHERE script_id=%s",
                (item.code,))
        self.db.commit()

    def agent(self, user_id: str) -> dict | None:
        return self._one(
            "SELECT user AS user_id,full_name,user_group,active,closer_campaigns "
            "FROM vicidial_users WHERE user=%s", (user_id,))

    def sync_agent_disabled(self, spec: AgentSpec) -> dict:
        current = self.agent(spec.user_id)
        if current and current["full_name"] != spec.full_name:
            raise CollisionError(f"incompatible collision: {spec.user_id}")
        if current:
            self._execute(
                "UPDATE vicidial_users SET active='N',user_group=%s,closer_campaigns=%s WHERE user=%s",
                (spec.user_group, " " + " ".join(spec.inbound_groups) + " -", spec.user_id))
        else:
            raise ValueError("agent creation requires the supported VICIdial Non-Agent API")
        self.db.commit()
        return self.agent(spec.user_id) or {}

    def disable_agent(self, user_id: str) -> dict:
        if not self.agent(user_id):
            raise ValueError("agent missing")
        self._execute("UPDATE vicidial_users SET active='N' WHERE user=%s", (user_id,))
        self.db.commit()
        return self.agent(user_id) or {}

    def lead_by_external_id(self, external_id: str) -> dict | None:
        return self._one(
            "SELECT lead_id,vendor_lead_code AS external_id,list_id,phone_code,phone_number,"
            "first_name,last_name,status FROM vicidial_list WHERE vendor_lead_code=%s", (external_id,))

    def publish_lead(self, spec: LeadSpec) -> dict:
        prior = self.lead_by_external_id(spec.external_id)
        if prior:
            for field, value in spec.model_dump(mode="json").items():
                if str(prior.get(field)) != str(value):
                    raise CollisionError("external lead id collision")
            return prior
        self._execute(
            "INSERT INTO vicidial_list(entry_date,status,user,vendor_lead_code,list_id,"
            "gmt_offset_now,called_since_last_reset,phone_code,phone_number,first_name,last_name)"
            " VALUES(%s,%s,'CODESTRA',%s,%s,'0.00','N',%s,%s,%s,%s)",
            (datetime.now(timezone.utc).replace(tzinfo=None), spec.status, spec.external_id,
             spec.list_id, spec.phone_code, spec.phone_number, spec.first_name, spec.last_name))
        self.db.commit()
        return self.lead_by_external_id(spec.external_id) or {}

    def callback(self, callback_id: int) -> dict | None:
        return self._one(
            "SELECT callback_id,lead_id,campaign_id,status,callback_time,user,recipient,comments "
            "FROM vicidial_callbacks WHERE callback_id=%s", (callback_id,))

    def callback_by_external_id(self, external_id: str) -> dict | None:
        return self._one(
            "SELECT callback_id,lead_id,campaign_id,status,callback_time,user,recipient,comments "
            "FROM vicidial_callbacks WHERE comments LIKE %s", (f"CODESTRA:{external_id}:%",))

    def create_callback(self, spec: CallbackSpec) -> dict:
        prior = self.callback_by_external_id(spec.external_id)
        if prior:
            marker = str(prior.get("comments", "")).split(":", 3)
            if len(marker) < 3 or marker[2] != callback_payload_hash(spec):
                raise CollisionError("callback external id collision")
            return prior
        comments = (
            f"CODESTRA:{spec.external_id}:{callback_payload_hash(spec)}:"
            f"{spec.comments}"
        )[:255]
        self._execute(
            "INSERT INTO vicidial_callbacks(lead_id,campaign_id,status,entry_time,callback_time,"
            "user,recipient,comments) VALUES(%s,%s,'ACTIVE',%s,%s,%s,%s,%s)",
            (spec.lead_id, spec.campaign_id, datetime.now(timezone.utc).replace(tzinfo=None),
             spec.callback_time.replace(tzinfo=None), spec.user_id, spec.recipient, comments))
        self.db.commit()
        return self.callback_by_external_id(spec.external_id) or {}

    def inventory(self) -> dict:
        tables = {
            "campaigns": "vicidial_campaigns", "lists": "vicidial_lists",
            "user_groups": "vicidial_user_groups", "inbound_groups": "vicidial_inbound_groups",
            "scripts": "vicidial_scripts", "dispositions": "vicidial_campaign_statuses",
            "agents": "vicidial_users", "leads": "vicidial_list", "callbacks": "vicidial_callbacks",
        }
        return {name: self._one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
                for name, table in tables.items()}
