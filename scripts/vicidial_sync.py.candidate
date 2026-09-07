#!/usr/bin/env python3
import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime

import phonenumbers
import pymysql
from dotenv import load_dotenv
from canonical_event_client import (
    CANONICAL_PATH,
    CANONICAL_URL,
    ContractError,
    EventClient,
    UnsupportedCapabilityError,
    load_secret_file as load_hmac_secret_file,
    map_agent_status,
    map_call_ended,
    map_disposition,
    map_recording,
)


BASE_DIR = "/opt/codestra-vicidial-sync"
ENV_PATH = os.path.join(BASE_DIR, "config", ".env")
LOG_PATH = os.path.join(BASE_DIR, "logs", "vicidial_sync.log")
PRODUCTION_WATERMARK_PATH = os.path.join(
    BASE_DIR, "config", "production-call-ended-watermark.json"
)
SYNC_DB_NAME = "codestra_vicidial_sync"
SEND_ONE_TEST_SOURCE_TABLE = "codex_service_send_test"
TEST_SYN_CAMPAIGN_ID = "TEST_SYN"
TEST_SYN_MAX_EVENTS = 10
TEST_SYN_ALLOWED_SOURCE_TABLES = (
    "vicidial_log",
    "vicidial_closer_log",
    "recording_log",
)
PRODUCTION_SOURCE_TABLES = (
    "production_vicidial_log",
    "production_vicidial_closer_log",
)
PRODUCTION_EVENT_TYPE = "call_end"
PRODUCTION_BACKLOG_LIMIT = 1000


def load_env():
    load_dotenv(ENV_PATH)
    api_pass_file = os.getenv("VICIDIAL_API_PASS_FILE", "")
    return {
        "db_host": os.getenv("VICIDIAL_DB_HOST", "127.0.0.1"),
        "db_socket": os.getenv("VICIDIAL_DB_SOCKET", ""),
        "db_port": int(os.getenv("VICIDIAL_DB_PORT", "3306")),
        "db_name": os.getenv("VICIDIAL_DB_NAME", "asterisk"),
        "db_user": os.getenv("VICIDIAL_DB_USER", "codestra_sync"),
        "db_pass": os.getenv("VICIDIAL_DB_PASS", ""),
        "middleware_event_url": os.getenv("MIDDLEWARE_EVENT_URL", CANONICAL_URL),
        "callback_hmac_file": os.getenv("VICIDIAL_CALLBACK_HMAC_FILE", ""),
        "client_instance_id": os.getenv("VICIDIAL_CLIENT_INSTANCE_ID", ""),
        "mtls_client_cert_file": os.getenv("VICIDIAL_MTLS_CLIENT_CERT_FILE", ""),
        "mtls_client_key_file": os.getenv("VICIDIAL_MTLS_CLIENT_KEY_FILE", ""),
        "mtls_ca_bundle_file": os.getenv("VICIDIAL_MTLS_CA_BUNDLE_FILE", ""),
        "vicidial_api_user": os.getenv("VICIDIAL_API_USER", ""),
        "vicidial_api_pass_file": api_pass_file,
        "vicidial_api_pass": load_secret_file(api_pass_file)
        if api_pass_file
        else os.getenv("VICIDIAL_API_PASS", ""),
        "default_country_code": os.getenv("DEFAULT_COUNTRY_CODE", "DO"),
        "batch_size": min(100, int(os.getenv("SYNC_BATCH_SIZE", "100"))),
        "interval_seconds": int(os.getenv("SYNC_INTERVAL_SECONDS", "15")),
        "send_events": os.getenv("SEND_EVENTS", "false").lower()
        in ("1", "true", "yes", "y"),
        "delivery_kill_switch": os.getenv(
            "DELIVERY_KILL_SWITCH", "/etc/codestra/disable-external-delivery"
        ),
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "transport_max_attempts": min(
            5, max(1, int(os.getenv("TRANSPORT_MAX_ATTEMPTS", "1")))
        ),
    }


def external_delivery_enabled(config):
    """Fail closed when configuration or the emergency kill switch disables delivery."""
    if not config["send_events"]:
        return False
    kill_switch = config.get("delivery_kill_switch")
    if kill_switch and os.path.exists(kill_switch):
        logging.critical("external delivery disabled by kill switch: %s", kill_switch)
        return False
    return True


def load_secret_file(path):
    if not path:
        return ""
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except IOError:
        logging.warning("Secret file is not readable: %s", path)
        return ""


def setup_logging(level):
    if not os.path.isdir(os.path.dirname(LOG_PATH)):
        os.makedirs(os.path.dirname(LOG_PATH))
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
    )


def connect_vicidial_db(config):
    kwargs = {
        "host": config["db_host"],
        "port": config["db_port"],
        "user": config["db_user"],
        "password": config["db_pass"],
        "database": config["db_name"],
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }
    if config["db_socket"]:
        kwargs["unix_socket"] = config["db_socket"]
    return pymysql.connect(**kwargs)


def connect_sync_db(config):
    kwargs = {
        "host": config["db_host"],
        "port": config["db_port"],
        "user": config["db_user"],
        "password": config["db_pass"],
        "database": SYNC_DB_NAME,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }
    if config["db_socket"]:
        kwargs["unix_socket"] = config["db_socket"]
    return pymysql.connect(**kwargs)


def normalize_phone(raw, default_country):
    if not raw:
        return None
    value = str(raw).strip()
    try:
        parsed = phonenumbers.parse(value, default_country)
        if phonenumbers.is_possible_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
    except Exception:
        return value
    return value


def create_event_uuid():
    return str(uuid.uuid4())


def as_text(value):
    if value is None:
        return None
    return str(value)


def iso_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_state(sync_conn, sync_name):
    with sync_conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM sync_state WHERE sync_name=%s FOR UPDATE",
            (sync_name,),
        )
        row = cur.fetchone()
        if row:
            return row
        cur.execute(
            "INSERT INTO sync_state (sync_name,last_id,last_timestamp) VALUES (%s,0,'1970-01-01 00:00:00')",
            (sync_name,),
        )
        return {
            "sync_name": sync_name,
            "last_id": 0,
            "last_timestamp": datetime(1970, 1, 1),
        }


def update_state(
    sync_conn, sync_name, last_id=None, last_uniqueid=None, last_timestamp=None
):
    with sync_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sync_state
            SET last_id=COALESCE(%s,last_id),
                last_uniqueid=COALESCE(%s,last_uniqueid),
                last_timestamp=COALESCE(%s,last_timestamp)
            WHERE sync_name=%s
            """,
            (last_id, last_uniqueid, last_timestamp, sync_name),
        )


def enqueue_event(sync_conn, event_type, source_table, source_id, payload):
    event_uuid = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, "codestra:{}:{}:{}".format(
            event_type, source_table, source_id
        )))
        if source_table in PRODUCTION_SOURCE_TABLES
        else create_event_uuid()
    )
    with sync_conn.cursor() as cur:
        cur.execute(
            """
            INSERT IGNORE INTO event_queue
            (event_uuid,event_type,source_table,source_id,uniqueid,lead_id,campaign_id,user,phone_number,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                event_uuid,
                event_type,
                source_table,
                as_text(source_id),
                as_text(payload.get("uniqueid")),
                as_text(payload.get("lead_id")),
                as_text(payload.get("campaign_id")),
                as_text(payload.get("user")),
                as_text(payload.get("phone_number")),
                json.dumps(payload, sort_keys=True, default=str),
            ),
        )
        return cur.rowcount == 1


def mark_event_sent(sync_conn, event_id):
    with sync_conn.cursor() as cur:
        cur.execute(
            "UPDATE event_queue SET status='sent', sent_at=NOW(), error_message=NULL WHERE id=%s",
            (event_id,),
        )


def mark_event_failed(sync_conn, event_id, error_message):
    sanitized = str(error_message)[:2000]
    with sync_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE event_queue
            SET status=IF(retry_count + 1 >= max_retries, 'failed', 'retrying'),
                retry_count=retry_count + 1,
                error_message=%s
            WHERE id=%s
            """,
            (sanitized, event_id),
        )


def mark_event_capability_rejected(sync_conn, event_id):
    with sync_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE event_queue
            SET status='failed',
                retry_count=max_retries,
                error_message='callback timezone provenance unavailable'
            WHERE id=%s
            """,
            (event_id,),
        )


def log_api_request(sync_conn, event, endpoint, response=None, error_message=None):
    response_status = response.status_code if response is not None else None
    response_body = None
    success = 1 if response is not None and 200 <= response.status_code < 300 else 0
    with sync_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_request_log
            (event_uuid,endpoint,method,request_payload,response_status,response_body,success,error_message)
            VALUES (%s,%s,'POST',%s,%s,%s,%s,%s)
            """,
            (
                event["event_uuid"],
                endpoint,
                json.dumps(
                    {
                        "event_uuid": event.get("event_uuid"),
                        "event_type": event.get("event_type"),
                    },
                    sort_keys=True,
                ),
                response_status,
                response_body,
                success,
                str(error_message)[:2000] if error_message else None,
            ),
        )


def build_canonical_event(event):
    raw = json.loads(event["payload"])
    event_id = event.get("event_uuid")
    correlation_id = "vicidial:{}".format(event_id)
    event_type = event.get("event_type")
    if event_type == "call_end":
        return map_call_ended(
            raw, event_id, correlation_id, raw.get("direction", "unknown")
        )
    if event_type == "disposition":
        return map_disposition(raw, event_id, correlation_id)
    if event_type == "recording":
        return map_recording(raw, event_id, correlation_id)
    if event_type == "callback":
        raise UnsupportedCapabilityError("callback timezone provenance unavailable")
    if event_type == "agent_status":
        return map_agent_status(raw, event_id, correlation_id)
    raise ContractError("unsupported queued event type")


def validate_delivery_config(config):
    if config["middleware_event_url"] != CANONICAL_URL:
        raise ContractError("callback destination is not approved")
    if not config["client_instance_id"]:
        raise ContractError("client-instance ID is required")
    return load_hmac_secret_file(config["callback_hmac_file"])


def send_event_to_middleware(sync_conn, config, event):
    if not external_delivery_enabled(config):
        raise RuntimeError("external event delivery is disabled")
    canonical = build_canonical_event(event)
    secret = validate_delivery_config(config)
    client = EventClient(
        secret,
        config["client_instance_id"],
        send_events=True,
        client_cert_file=config["mtls_client_cert_file"],
        client_key_file=config["mtls_client_key_file"],
        ca_bundle_file=config["mtls_ca_bundle_file"],
        max_attempts=config["transport_max_attempts"],
    )
    prepared = client.prepare(canonical)
    result = client.deliver(prepared)
    log_api_request(sync_conn, event, CANONICAL_PATH)
    return result


def send_one_test_event():
    config = load_env()
    setup_logging(config["log_level"])
    sync_conn = connect_sync_db(config)
    try:
        with sync_conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM event_queue
                WHERE status='pending' AND source_table=%s
                ORDER BY id
                """,
                (SEND_ONE_TEST_SOURCE_TABLE,),
            )
            events = cur.fetchall()

        if len(events) != 1:
            raise RuntimeError(
                "Expected exactly one pending {} event, found {}".format(
                    SEND_ONE_TEST_SOURCE_TABLE,
                    len(events),
                )
            )

        event = events[0]
        try:
            response = send_event_to_middleware(sync_conn, config, event)
            if not response.get("sent"):
                raise RuntimeError("event delivery is disabled")
            mark_event_sent(sync_conn, event["id"])
            sync_conn.commit()
            print("accepted")
        except Exception as exc:
            logging.exception(
                "Failed sending test event id=%s type=%s",
                event["id"],
                event["event_type"],
            )
            try:
                log_api_request(sync_conn, event, CANONICAL_PATH, error_message=exc)
                with sync_conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE event_queue
                        SET status='failed',
                            retry_count=retry_count + 1,
                            error_message=%s
                        WHERE id=%s
                        """,
                        (str(exc)[:2000], event["id"]),
                    )
                sync_conn.commit()
            except Exception:
                sync_conn.rollback()
                logging.exception(
                    "Failed updating failed state for test event id=%s", event["id"]
                )
            raise
    except Exception:
        sync_conn.rollback()
        raise
    finally:
        sync_conn.close()


def enqueue_test_syn_events():
    config = load_env()
    setup_logging(config["log_level"])
    vici_conn = connect_vicidial_db(config)
    sync_conn = connect_sync_db(config)
    queued = 0
    try:
        with vici_conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM vicidial_log
                WHERE campaign_id=%s
                ORDER BY call_date DESC, uniqueid DESC
                LIMIT %s
                """,
                (TEST_SYN_CAMPAIGN_ID, TEST_SYN_MAX_EVENTS),
            )
            outbound_rows = cur.fetchall()

            cur.execute(
                """
                SELECT *
                FROM vicidial_closer_log
                WHERE campaign_id=%s
                ORDER BY closecallid DESC
                LIMIT %s
                """,
                (TEST_SYN_CAMPAIGN_ID, TEST_SYN_MAX_EVENTS),
            )
            inbound_rows = cur.fetchall()

            cur.execute(
                """
                SELECT r.*, l.phone_number, vl.campaign_id
                FROM recording_log r
                INNER JOIN vicidial_list l ON l.lead_id = r.lead_id
                INNER JOIN vicidial_lists vl ON vl.list_id = l.list_id
                WHERE vl.campaign_id=%s
                ORDER BY r.recording_id DESC
                LIMIT %s
                """,
                (TEST_SYN_CAMPAIGN_ID, TEST_SYN_MAX_EVENTS),
            )
            recording_rows = cur.fetchall()

        for row in outbound_rows:
            if queued >= TEST_SYN_MAX_EVENTS:
                break
            payload = build_call_payload(row, "outbound", "vicidial_log")
            payload["normalized_phone"] = normalize_phone(
                payload["phone_number"], config["default_country_code"]
            )
            if enqueue_event(
                sync_conn, "call_end", "vicidial_log", row["uniqueid"], payload
            ):
                queued += 1
            if queued >= TEST_SYN_MAX_EVENTS:
                break
            dispo_payload = dict(payload)
            dispo_payload["event_type"] = "disposition"
            if enqueue_event(
                sync_conn, "disposition", "vicidial_log", row["uniqueid"], dispo_payload
            ):
                queued += 1

        for row in inbound_rows:
            if queued >= TEST_SYN_MAX_EVENTS:
                break
            payload = build_call_payload(row, "inbound", "vicidial_closer_log")
            payload["closecallid"] = row.get("closecallid")
            payload["normalized_phone"] = normalize_phone(
                payload["phone_number"], config["default_country_code"]
            )
            if enqueue_event(
                sync_conn,
                "call_end",
                "vicidial_closer_log",
                row["closecallid"],
                payload,
            ):
                queued += 1
            if queued >= TEST_SYN_MAX_EVENTS:
                break
            dispo_payload = dict(payload)
            dispo_payload["event_type"] = "disposition"
            if enqueue_event(
                sync_conn,
                "disposition",
                "vicidial_closer_log",
                row["closecallid"],
                dispo_payload,
            ):
                queued += 1

        for row in recording_rows:
            if queued >= TEST_SYN_MAX_EVENTS:
                break
            payload = {
                "event_type": "recording",
                "recording_id": as_text(row.get("recording_id")),
                "uniqueid": as_text(row.get("vicidial_id")),
                "lead_id": as_text(row.get("lead_id")),
                "campaign_id": as_text(row.get("campaign_id")),
                "user": as_text(row.get("user")),
                "phone_number": as_text(row.get("phone_number")),
                "normalized_phone": normalize_phone(
                    row.get("phone_number"), config["default_country_code"]
                ),
                "channel": as_text(row.get("channel")),
                "server_ip": as_text(row.get("server_ip")),
                "extension": as_text(row.get("extension")),
                "start_time": iso_datetime(row.get("start_time")),
                "start_epoch": row.get("start_epoch"),
                "end_time": iso_datetime(row.get("end_time")),
                "end_epoch": row.get("end_epoch"),
                "length_in_sec": row.get("length_in_sec"),
                "filename": as_text(row.get("filename")),
                "recording_url": as_text(row.get("location")),
                "source_table": "recording_log",
            }
            if enqueue_event(
                sync_conn, "recording", "recording_log", row["recording_id"], payload
            ):
                queued += 1

        sync_conn.commit()
        print("queued={}".format(queued))
    except Exception:
        sync_conn.rollback()
        logging.exception("TEST_SYN queue run failed")
        raise
    finally:
        sync_conn.close()
        vici_conn.close()


def send_test_syn_batch():
    config = load_env()
    setup_logging(config["log_level"])
    sync_conn = connect_sync_db(config)
    sent = 0
    try:
        with sync_conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM event_queue
                WHERE status IN ('pending','retrying')
                  AND retry_count < max_retries
                  AND campaign_id=%s
                  AND source_table IN (%s,%s,%s)
                ORDER BY id
                LIMIT %s
                """,
                (TEST_SYN_CAMPAIGN_ID,)
                + TEST_SYN_ALLOWED_SOURCE_TABLES
                + (TEST_SYN_MAX_EVENTS,),
            )
            events = cur.fetchall()

        for event in events:
            if sent >= TEST_SYN_MAX_EVENTS:
                break
            payload = json.loads(event["payload"])
            if payload.get("campaign_id") != TEST_SYN_CAMPAIGN_ID:
                raise RuntimeError(
                    "Refusing non-TEST_SYN event id={}".format(event["id"])
                )
            if event.get("source_table") not in TEST_SYN_ALLOWED_SOURCE_TABLES:
                raise RuntimeError(
                    "Refusing disallowed source_table event id={}".format(event["id"])
                )
            try:
                send_event_to_middleware(sync_conn, config, event)
                mark_event_sent(sync_conn, event["id"])
                sync_conn.commit()
                sent += 1
            except Exception as exc:
                logging.exception(
                    "Failed sending TEST_SYN event id=%s type=%s",
                    event["id"],
                    event["event_type"],
                )
                try:
                    log_api_request(sync_conn, event, CANONICAL_PATH, error_message=exc)
                    mark_event_failed(sync_conn, event["id"], exc)
                    sync_conn.commit()
                except Exception:
                    sync_conn.rollback()
                    logging.exception(
                        "Failed updating TEST_SYN retry state for event id=%s",
                        event["id"],
                    )
        print("sent={}".format(sent))
    finally:
        sync_conn.close()




def production_backlog_count(sync_conn):
    """Count the complete eligible production queue before selecting a batch."""
    with sync_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS backlog_count
            FROM event_queue
            WHERE status IN ('pending','retrying')
              AND retry_count < max_retries
              AND event_type=%s
              AND source_table IN (%s,%s)
            """,
            (PRODUCTION_EVENT_TYPE,) + PRODUCTION_SOURCE_TABLES,
        )
        row = cur.fetchone()
    if not row or "backlog_count" not in row:
        raise RuntimeError("production backlog count is unavailable")
    try:
        count = int(row["backlog_count"])
    except (TypeError, ValueError):
        raise RuntimeError("production backlog count is invalid")
    if count < 0:
        raise RuntimeError("production backlog count is invalid")
    return count


def process_pending_events(sync_conn, config):
    if not external_delivery_enabled(config):
        logging.debug("external delivery disabled; queued events will not be posted yet")
        return
    backlog_count = production_backlog_count(sync_conn)
    if backlog_count > PRODUCTION_BACKLOG_LIMIT:
        raise RuntimeError("production backlog exceeds hard limit")
    with sync_conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM event_queue
            WHERE status IN ('pending','retrying')
              AND retry_count < max_retries
              AND event_type=%s
              AND source_table IN (%s,%s)
            ORDER BY id
            LIMIT %s
            """,
            (PRODUCTION_EVENT_TYPE,)
            + PRODUCTION_SOURCE_TABLES
            + (config["batch_size"],),
        )
        events = cur.fetchall()
    for event in events:
        try:
            send_event_to_middleware(sync_conn, config, event)
            mark_event_sent(sync_conn, event["id"])
            sync_conn.commit()
        except UnsupportedCapabilityError:
            logging.error(
                "Event capability rejected id=%s type=%s: callback timezone provenance unavailable",
                event["id"],
                event["event_type"],
            )
            mark_event_capability_rejected(sync_conn, event["id"])
            sync_conn.commit()
        except Exception as exc:
            logging.exception(
                "Failed sending event id=%s type=%s", event["id"], event["event_type"]
            )
            try:
                log_api_request(sync_conn, event, CANONICAL_PATH, error_message=exc)
                mark_event_failed(sync_conn, event["id"], exc)
                sync_conn.commit()
            except Exception:
                sync_conn.rollback()
                logging.exception(
                    "Failed updating retry state for event id=%s", event["id"]
                )


def build_call_payload(row, direction, source_table):
    payload = {
        "event_type": "call_end",
        "uniqueid": as_text(row.get("uniqueid")),
        "lead_id": as_text(row.get("lead_id")),
        "list_id": as_text(row.get("list_id")),
        "campaign_id": as_text(row.get("campaign_id")),
        "user": as_text(row.get("user")),
        "phone_number": as_text(row.get("phone_number")),
        "phone_code": as_text(row.get("phone_code")),
        "status": as_text(row.get("status")),
        "call_date": iso_datetime(row.get("call_date")),
        "start_epoch": row.get("start_epoch"),
        "end_epoch": row.get("end_epoch"),
        "length_in_sec": row.get("length_in_sec"),
        "comments": as_text(row.get("comments")),
        "processed": as_text(row.get("processed")),
        "queue_seconds": float(row["queue_seconds"])
        if row.get("queue_seconds") is not None
        else None,
        "called_count": row.get("called_count"),
        "direction": direction,
        "source_table": source_table,
    }
    return payload


def load_production_watermark():
    st = os.stat(PRODUCTION_WATERMARK_PATH)
    if st.st_uid != 0 or st.st_mode & 0o077:
        raise RuntimeError("production watermark must be root-owned and private")
    with open(PRODUCTION_WATERMARK_PATH, "r") as handle:
        value = json.load(handle)
    if value.get("rule") != "strictly_greater_than_composite_cursor":
        raise RuntimeError("invalid production watermark rule")
    expected = {"vicidial_log", "vicidial_closer_log"}
    if set(value.get("sources", {})) != expected:
        raise RuntimeError("incomplete production watermark")
    return value


def get_production_state(sync_conn, sync_name, initial):
    with sync_conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM sync_state WHERE sync_name=%s FOR UPDATE",
            (sync_name,),
        )
        row = cur.fetchone()
        if row:
            if int(row["last_id"] or 0) < int(initial["end_epoch"]):
                raise RuntimeError("checkpoint regression below hard watermark")
            return row
        cur.execute(
            """INSERT INTO sync_state
               (sync_name,last_id,last_uniqueid,last_timestamp)
               VALUES (%s,%s,%s,%s)""",
            (
                sync_name,
                int(initial["end_epoch"]),
                str(initial["source_id"]),
                initial["call_date"],
            ),
        )
        return {
            "sync_name": sync_name,
            "last_id": int(initial["end_epoch"]),
            "last_uniqueid": str(initial["source_id"]),
            "last_timestamp": initial["call_date"],
        }


def minimal_call_payload(row, direction, source_table):
    allowed = {
        "event_type": "call_end",
        "uniqueid": as_text(row.get("uniqueid")),
        "lead_id": row.get("lead_id"),
        "campaign_id": as_text(row.get("campaign_id")),
        "user": as_text(row.get("user")),
        "status": as_text(row.get("status")),
        "start_epoch": row.get("start_epoch"),
        "end_epoch": row.get("end_epoch"),
        "length_in_sec": row.get("length_in_sec"),
        "direction": direction,
        "source_table": source_table,
    }
    forbidden = {
        "phone_number", "phone_code", "customer_name", "email", "address",
        "comments", "recording_content", "recording_url",
    }
    if set(allowed) & forbidden:
        raise RuntimeError("sensitive source field selected")
    return {key: value for key, value in allowed.items() if value is not None}


def sync_production_call_ended(vici_conn, sync_conn, config):
    watermark = load_production_watermark()
    definitions = (
        {
            "table": "vicidial_log",
            "key": "uniqueid",
            "direction": "outbound",
            "queue_source": "production_vicidial_log",
        },
        {
            "table": "vicidial_closer_log",
            "key": "closecallid",
            "direction": "inbound",
            "queue_source": "production_vicidial_closer_log",
        },
    )
    total = 0
    for definition in definitions:
        table = definition["table"]
        key = definition["key"]
        initial = watermark["sources"][table]
        state_name = "production_call_ended:{}".format(table)
        state = get_production_state(sync_conn, state_name, initial)
        floor_epoch = int(state["last_id"])
        floor_source = state["last_uniqueid"] or initial["source_id"]
        comparator = key if table == "vicidial_log" else "CAST({} AS UNSIGNED)".format(key)
        with vici_conn.cursor() as cur:
            cur.execute(
                """
                SELECT {key} AS source_id, uniqueid, lead_id, campaign_id, user,
                       status, start_epoch, end_epoch, length_in_sec, call_date
                FROM {table}
                WHERE call_date >= %s
                  AND end_epoch IS NOT NULL AND end_epoch > 0
                  AND uniqueid IS NOT NULL AND uniqueid <> ''
                  AND status IS NOT NULL AND status <> ''
                  AND length_in_sec IS NOT NULL
                  AND (end_epoch > %s OR (end_epoch = %s AND {comparator} > %s))
                ORDER BY end_epoch,{comparator}
                LIMIT %s
                """.format(key=key, table=table, comparator=comparator),
                (
                    initial["call_date"],
                    floor_epoch,
                    floor_epoch,
                    floor_source,
                    min(config["batch_size"], 10),
                ),
            )
            rows = cur.fetchall()
        for row in rows:
            source_id = str(row["source_id"])
            payload = minimal_call_payload(
                row, definition["direction"], definition["queue_source"]
            )
            enqueue_event(
                sync_conn,
                PRODUCTION_EVENT_TYPE,
                definition["queue_source"],
                source_id,
                payload,
            )
            update_state(
                sync_conn,
                state_name,
                last_id=int(row["end_epoch"]),
                last_uniqueid=source_id,
                last_timestamp=row["call_date"],
            )
            total += 1
    return total


def sync_outbound_calls(vici_conn, sync_conn, config):
    state = get_state(sync_conn, "vicidial_log")
    with vici_conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM vicidial_log
            WHERE call_date > %s
            ORDER BY call_date, uniqueid
            LIMIT %s
            """,
            (state["last_timestamp"], config["batch_size"]),
        )
        rows = cur.fetchall()
    max_ts = state["last_timestamp"]
    for row in rows:
        payload = build_call_payload(row, "outbound", "vicidial_log")
        payload["normalized_phone"] = normalize_phone(
            payload["phone_number"], config["default_country_code"]
        )
        enqueue_event(sync_conn, "call_end", "vicidial_log", row["uniqueid"], payload)
        dispo_payload = dict(payload)
        dispo_payload["event_type"] = "disposition"
        enqueue_event(
            sync_conn, "disposition", "vicidial_log", row["uniqueid"], dispo_payload
        )
        if row.get("call_date") and row["call_date"] > max_ts:
            max_ts = row["call_date"]
    if rows:
        update_state(sync_conn, "vicidial_log", last_timestamp=max_ts)


def sync_inbound_calls(vici_conn, sync_conn, config):
    state = get_state(sync_conn, "vicidial_closer_log")
    with vici_conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM vicidial_closer_log
            WHERE closecallid > %s
            ORDER BY closecallid
            LIMIT %s
            """,
            (state["last_id"] or 0, config["batch_size"]),
        )
        rows = cur.fetchall()
    max_id = state["last_id"] or 0
    max_ts = state["last_timestamp"]
    for row in rows:
        payload = build_call_payload(row, "inbound", "vicidial_closer_log")
        payload["closecallid"] = row.get("closecallid")
        payload["normalized_phone"] = normalize_phone(
            payload["phone_number"], config["default_country_code"]
        )
        enqueue_event(
            sync_conn, "call_end", "vicidial_closer_log", row["closecallid"], payload
        )
        dispo_payload = dict(payload)
        dispo_payload["event_type"] = "disposition"
        enqueue_event(
            sync_conn,
            "disposition",
            "vicidial_closer_log",
            row["closecallid"],
            dispo_payload,
        )
        max_id = max(max_id, row["closecallid"])
        if row.get("call_date") and (not max_ts or row["call_date"] > max_ts):
            max_ts = row["call_date"]
    if rows:
        update_state(
            sync_conn, "vicidial_closer_log", last_id=max_id, last_timestamp=max_ts
        )


def sync_recordings(vici_conn, sync_conn, config):
    state = get_state(sync_conn, "recording_log")
    with vici_conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.*, l.phone_number, vl.campaign_id
            FROM recording_log r
            LEFT JOIN vicidial_list l ON l.lead_id = r.lead_id
            LEFT JOIN vicidial_lists vl ON vl.list_id = l.list_id
            WHERE r.recording_id > %s
            ORDER BY r.recording_id
            LIMIT %s
            """,
            (state["last_id"] or 0, config["batch_size"]),
        )
        rows = cur.fetchall()
    max_id = state["last_id"] or 0
    max_ts = state["last_timestamp"]
    for row in rows:
        payload = {
            "event_type": "recording",
            "recording_id": as_text(row.get("recording_id")),
            "uniqueid": as_text(row.get("vicidial_id")),
            "lead_id": as_text(row.get("lead_id")),
            "campaign_id": as_text(row.get("campaign_id")),
            "user": as_text(row.get("user")),
            "phone_number": as_text(row.get("phone_number")),
            "normalized_phone": normalize_phone(
                row.get("phone_number"), config["default_country_code"]
            ),
            "channel": as_text(row.get("channel")),
            "server_ip": as_text(row.get("server_ip")),
            "extension": as_text(row.get("extension")),
            "start_time": iso_datetime(row.get("start_time")),
            "start_epoch": row.get("start_epoch"),
            "end_time": iso_datetime(row.get("end_time")),
            "end_epoch": row.get("end_epoch"),
            "length_in_sec": row.get("length_in_sec"),
            "filename": as_text(row.get("filename")),
            "recording_url": as_text(row.get("location")),
            "source_table": "recording_log",
        }
        enqueue_event(
            sync_conn, "recording", "recording_log", row["recording_id"], payload
        )
        max_id = max(max_id, row["recording_id"])
        if row.get("start_time") and (not max_ts or row["start_time"] > max_ts):
            max_ts = row["start_time"]
    if rows:
        update_state(sync_conn, "recording_log", last_id=max_id, last_timestamp=max_ts)


def sync_agent_status(vici_conn, sync_conn, config):
    with vici_conn.cursor() as cur:
        cur.execute(
            """
            SELECT user,campaign_id,status,lead_id,uniqueid,last_call_time,
                   UNIX_TIMESTAMP(last_update_time) AS last_update_epoch,
                   last_call_finish,pause_code
            FROM vicidial_live_agents
            ORDER BY user
            LIMIT %s
            """,
            (config["batch_size"],),
        )
        rows = cur.fetchall()
    status_map = {
        "READY": "ready",
        "QUEUE": "ready",
        "INCALL": "in_call",
        "CLOSER": "in_call",
        "PAUSED": "paused",
        "MQUEUE": "wrap_up",
    }
    for row in rows:
        source_id = "{}:{}:{}".format(
            row.get("user"),
            row.get("status"),
            as_text(row.get("last_update_epoch")),
        )
        payload = {
            "event_type": "agent_status",
            "user": as_text(row.get("user")),
            "campaign_id": as_text(row.get("campaign_id")),
            "status": as_text(row.get("status")),
            "mapped_status": status_map.get(row.get("status"), "offline"),
            "lead_id": as_text(row.get("lead_id")),
            "uniqueid": as_text(row.get("uniqueid")),
            "pause_code": as_text(row.get("pause_code")),
            "last_update_epoch": row.get("last_update_epoch"),
            "last_call_time": iso_datetime(row.get("last_call_time")),
            "last_call_finish": iso_datetime(row.get("last_call_finish")),
            "server_time": datetime.now().isoformat(),
            "source_table": "vicidial_live_agents",
        }
        enqueue_event(
            sync_conn, "agent_status", "vicidial_live_agents", source_id, payload
        )


def run_once():
    config = load_env()
    setup_logging(config["log_level"])
    validate_delivery_config(config)
    vici_conn = connect_vicidial_db(config)
    sync_conn = connect_sync_db(config)
    try:
        sync_production_call_ended(vici_conn, sync_conn, config)
        sync_conn.commit()
        process_pending_events(sync_conn, config)
        logging.info("sync run completed")
    except Exception:
        sync_conn.rollback()
        vici_conn.rollback()
        logging.exception("sync run failed")
        raise
    finally:
        sync_conn.close()
        vici_conn.close()


def run_loop():
    config = load_env()
    setup_logging(config["log_level"])
    interval = config["interval_seconds"]
    while True:
        try:
            run_once()
        except Exception:
            logging.exception("sync iteration failed; continuing")
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Codestra VICIdial sync worker")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument(
        "--send-one-test-event",
        action="store_true",
        help="Send exactly one pending codex_service_send_test event without enabling SEND_EVENTS",
    )
    parser.add_argument(
        "--queue-test-syn-events",
        action="store_true",
        help="Queue up to 10 TEST_SYN events from real VICIdial log tables without sending",
    )
    parser.add_argument(
        "--send-test-syn-batch",
        action="store_true",
        help="Send up to 10 pending TEST_SYN events without enabling SEND_EVENTS",
    )
    args = parser.parse_args()
    if args.send_one_test_event:
        send_one_test_event()
    elif args.queue_test_syn_events:
        enqueue_test_syn_events()
    elif args.send_test_syn_batch:
        send_test_syn_batch()
    elif args.loop:
        run_loop()
    else:
        run_once()


if __name__ == "__main__":
    main()
