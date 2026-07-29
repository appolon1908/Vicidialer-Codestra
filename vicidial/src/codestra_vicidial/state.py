from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS requests(
          operation TEXT NOT NULL, idempotency_key TEXT NOT NULL,
          request_hash TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(operation,idempotency_key));
        CREATE TABLE IF NOT EXISTS audit(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
          correlation_id TEXT NOT NULL, actor TEXT NOT NULL, operation TEXT NOT NULL,
          target_hash TEXT NOT NULL, outcome TEXT NOT NULL, detail_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mappings(
          kind TEXT NOT NULL, external_id TEXT NOT NULL, actual_id TEXT NOT NULL,
          payload_hash TEXT NOT NULL, PRIMARY KEY(kind,external_id));
        CREATE TABLE IF NOT EXISTS transfer_policies(
          campaign_id TEXT PRIMARY KEY, targets TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots(
          snapshot_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
          target_hash TEXT NOT NULL, state_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaign_policies(
          campaign_id TEXT PRIMARY KEY, transfer_targets TEXT NOT NULL,
          recording_policy_reference TEXT NOT NULL, reporting_mapping TEXT NOT NULL,
          callback_enabled INTEGER NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(
          evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
          operation TEXT NOT NULL, target_hash TEXT NOT NULL, outcome TEXT NOT NULL,
          redacted_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operation_runs(
          operation_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
          idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
          correlation_id TEXT NOT NULL, status TEXT NOT NULL,
          attempt INTEGER NOT NULL, started_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, result_json TEXT, error_type TEXT,
          UNIQUE(operation,idempotency_key));
        CREATE INDEX IF NOT EXISTS operation_runs_status
          ON operation_runs(status,updated_at);
        CREATE TABLE IF NOT EXISTS public_resources(
          kind TEXT NOT NULL, public_id TEXT NOT NULL, desired_json TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
          PRIMARY KEY(kind,public_id));
        """)
        self.db.commit()

    def put_resource(self, kind: str, public_id: str, desired: dict,
                     enabled: bool = False) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO public_resources VALUES(?,?,?,?,?) "
            "ON CONFLICT(kind,public_id) DO UPDATE SET "
            "desired_json=excluded.desired_json,enabled=excluded.enabled,"
            "updated_at=excluded.updated_at",
            (kind, public_id, json.dumps(desired, sort_keys=True),
             1 if enabled else 0, now),
        )
        self.db.commit()
        return self.resource(kind, public_id)

    def resource(self, kind: str, public_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT desired_json,enabled,updated_at FROM public_resources "
            "WHERE kind=? AND public_id=?", (kind, public_id)
        ).fetchone()
        if not row:
            return None
        desired = json.loads(row["desired_json"])
        return {
            "public_id": public_id, "kind": kind, "desired": desired,
            "enabled": bool(row["enabled"]), "updated_at": row["updated_at"],
            "readback_sha256": self.digest({
                "kind": kind, "public_id": public_id, "desired": desired,
                "enabled": bool(row["enabled"]),
            }),
        }

    def set_resource_enabled(self, kind: str, public_id: str,
                             enabled: bool) -> dict | None:
        self.db.execute(
            "UPDATE public_resources SET enabled=?,updated_at=? "
            "WHERE kind=? AND public_id=?",
            (1 if enabled else 0, datetime.now(timezone.utc).isoformat(),
             kind, public_id),
        )
        self.db.commit()
        return self.resource(kind, public_id)

    @staticmethod
    def digest(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    def prior(self, operation: str, key: str, request_hash: str) -> dict | None:
        row = self.db.execute(
            "SELECT request_hash,result FROM requests WHERE operation=? AND idempotency_key=?",
            (operation, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ValueError("idempotency conflict")
        return json.loads(row["result"])

    def record(self, operation: str, key: str, request_hash: str, result: dict) -> None:
        self.db.execute(
            "INSERT INTO requests VALUES(?,?,?,?,?)",
            (operation, key, request_hash, json.dumps(result, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def begin_operation(self, operation: str, key: str, request_hash: str,
                        correlation_id: str) -> str:
        operation_id = self.digest({"operation": operation, "key": key})[:32]
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO operation_runs(operation_id,operation,idempotency_key,"
            "request_hash,correlation_id,status,attempt,started_at,updated_at) "
            "VALUES(?,?,?,?,?,'running',1,?,?) "
            "ON CONFLICT(operation,idempotency_key) DO UPDATE SET "
            "status='running',attempt=operation_runs.attempt+1,"
            "updated_at=excluded.updated_at,error_type=NULL "
            "WHERE operation_runs.request_hash=excluded.request_hash",
            (operation_id, operation, key, request_hash, correlation_id, now, now),
        )
        row = self.db.execute(
            "SELECT operation_id,request_hash FROM operation_runs "
            "WHERE operation=? AND idempotency_key=?", (operation, key)
        ).fetchone()
        if row["request_hash"] != request_hash:
            raise ValueError("idempotency conflict")
        self.db.commit()
        return row["operation_id"]

    def finish_operation(self, operation_id: str, result: dict) -> None:
        self.db.execute(
            "UPDATE operation_runs SET status='succeeded',updated_at=?,"
            "result_json=?,error_type=NULL WHERE operation_id=?",
            (datetime.now(timezone.utc).isoformat(),
             json.dumps(result, sort_keys=True, default=str), operation_id),
        )
        self.db.commit()

    def fail_operation(self, operation_id: str, error_type: str) -> None:
        self.db.execute(
            "UPDATE operation_runs SET status='failed',updated_at=?,error_type=? "
            "WHERE operation_id=?",
            (datetime.now(timezone.utc).isoformat(), error_type, operation_id),
        )
        self.db.commit()

    def operation(self, operation_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM operation_runs WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "operation_id": row["operation_id"], "operation": row["operation"],
            "status": row["status"], "attempt": row["attempt"],
            "correlation_id": row["correlation_id"],
            "started_at": row["started_at"], "updated_at": row["updated_at"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_type": row["error_type"],
        }

    def recover_interrupted(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT operation_id,operation,idempotency_key,attempt FROM operation_runs "
            "WHERE status='running' ORDER BY started_at"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE operation_runs SET status='recovery_required',updated_at=? "
            "WHERE status='running'", (now,))
        self.db.commit()
        return [dict(row) for row in rows]

    def audit(self, correlation_id: str, actor: str, operation: str, target: str,
              outcome: str, detail: object) -> None:
        self.db.execute(
            "INSERT INTO audit(occurred_at,correlation_id,actor,operation,target_hash,outcome,detail_hash)"
            " VALUES(?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), correlation_id, actor, operation,
             self.digest(target), outcome, self.digest(detail)),
        )
        self.db.commit()

    def mapping(self, kind: str, external_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT actual_id,payload_hash FROM mappings WHERE kind=? AND external_id=?",
            (kind, external_id),
        ).fetchone()

    def put_mapping(self, kind: str, external_id: str, actual_id: str, payload_hash: str) -> None:
        self.db.execute(
            "INSERT INTO mappings(kind,external_id,actual_id,payload_hash) VALUES(?,?,?,?) "
            "ON CONFLICT(kind,external_id) DO UPDATE SET actual_id=excluded.actual_id,payload_hash=excluded.payload_hash",
            (kind, external_id, actual_id, payload_hash),
        )
        self.db.commit()

    def put_transfer_policy(self, campaign_id: str, targets: list[str]) -> None:
        self.db.execute(
            "INSERT INTO transfer_policies VALUES(?,?,?) ON CONFLICT(campaign_id) "
            "DO UPDATE SET targets=excluded.targets,updated_at=excluded.updated_at",
            (campaign_id, json.dumps(sorted(targets)), datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def transfer_policy(self, campaign_id: str) -> list[str] | None:
        row = self.db.execute(
            "SELECT targets FROM transfer_policies WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        return json.loads(row["targets"]) if row else None

    def put_campaign_policy(self, campaign_id: str, transfer_targets: list[str],
                            recording_policy_reference: str,
                            reporting_mapping: str,
                            callback_enabled: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO campaign_policies VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(campaign_id) DO UPDATE SET "
            "transfer_targets=excluded.transfer_targets,"
            "recording_policy_reference=excluded.recording_policy_reference,"
            "reporting_mapping=excluded.reporting_mapping,"
            "callback_enabled=excluded.callback_enabled,"
            "updated_at=excluded.updated_at",
            (campaign_id, json.dumps(sorted(transfer_targets)),
             recording_policy_reference, reporting_mapping,
             1 if callback_enabled else 0, now),
        )
        self.db.commit()

    def campaign_policy(self, campaign_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT transfer_targets,recording_policy_reference,reporting_mapping,"
            "callback_enabled FROM campaign_policies WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "transfer_targets": json.loads(row["transfer_targets"]),
            "recording_policy_reference": row["recording_policy_reference"],
            "reporting_mapping": row["reporting_mapping"],
            "callback_enabled": bool(row["callback_enabled"]),
        }

    def record_evidence(self, operation: str, target: str, outcome: str,
                        redacted: object) -> None:
        self.db.execute(
            "INSERT INTO evidence(occurred_at,operation,target_hash,outcome,redacted_json)"
            " VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), operation, self.digest(target),
             outcome, json.dumps(redacted, sort_keys=True, default=str)),
        )
        self.db.commit()

    def latest_evidence(self, operation: str) -> dict | None:
        row = self.db.execute(
            "SELECT outcome,redacted_json FROM evidence WHERE operation=? "
            "ORDER BY evidence_id DESC LIMIT 1", (operation,)
        ).fetchone()
        if not row:
            return None
        return {
            "outcome": row["outcome"],
            "redacted": json.loads(row["redacted_json"]),
        }

    def snapshot(self, snapshot_id: str, operation: str, target: str, state: object) -> None:
        self.db.execute(
            "INSERT INTO snapshots VALUES(?,?,?,?,?)",
            (snapshot_id, operation, self.digest(target),
             json.dumps(state, sort_keys=True, default=str),
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT operation,target_hash,state_json,created_at FROM snapshots "
            "WHERE snapshot_id=?", (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "operation": row["operation"], "target_hash": row["target_hash"],
            "state": json.loads(row["state_json"]), "created_at": row["created_at"],
        }
