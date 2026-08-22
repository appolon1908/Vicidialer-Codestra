from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
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
        CREATE UNIQUE INDEX IF NOT EXISTS mappings_kind_actual
          ON mappings(kind,actual_id);
        CREATE TABLE IF NOT EXISTS campaign_desired(
          canonical_id TEXT PRIMARY KEY, actual_id TEXT NOT NULL UNIQUE,
          desired_json TEXT NOT NULL, desired_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS group_ownership(
          code TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
          business_unit TEXT NOT NULL, kind TEXT NOT NULL);
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
        """)
        self.db.commit()

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

    def mapping_for_actual(self, kind: str, actual_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT external_id,payload_hash FROM mappings "
            "WHERE kind=? AND actual_id=?", (kind, actual_id)
        ).fetchone()

    def put_mapping(self, kind: str, external_id: str, actual_id: str, payload_hash: str) -> None:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.mapping(kind, external_id)
            if current is not None:
                if current["actual_id"] != actual_id:
                    raise ValueError("ownership collision: canonical mapping changed")
                if current["payload_hash"] != payload_hash:
                    raise ValueError("mapping desired hash conflict")
                self.db.commit()
                return
            self.db.execute(
                "INSERT INTO mappings(kind,external_id,actual_id,payload_hash) "
                "VALUES(?,?,?,?)",
                (kind, external_id, actual_id, payload_hash),
            )
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise ValueError("ownership collision: actual mapping already claimed") from exc
        except Exception:
            self.db.rollback()
            raise

    def put_campaign_desired(self, canonical_id: str, actual_id: str,
                             desired: dict) -> None:
        payload_hash = self.digest(desired)
        current = self.db.execute(
            "SELECT actual_id,desired_hash FROM campaign_desired "
            "WHERE canonical_id=?", (canonical_id,)
        ).fetchone()
        if current:
            if current["actual_id"] != actual_id:
                raise ValueError("ownership collision: desired campaign remapped")
            if current["desired_hash"] != payload_hash:
                raise ValueError("campaign desired hash conflict")
            return
        self.db.execute(
            "INSERT INTO campaign_desired VALUES(?,?,?,?)",
            (canonical_id, actual_id, json.dumps(desired, sort_keys=True),
             payload_hash),
        )
        self.db.commit()

    def campaign_desired(self) -> list[dict]:
        return [
            {
                "canonical_id": row["canonical_id"],
                "actual_id": row["actual_id"],
                "desired": json.loads(row["desired_json"]),
                "desired_hash": row["desired_hash"],
            }
            for row in self.db.execute(
                "SELECT canonical_id,actual_id,desired_json,desired_hash "
                "FROM campaign_desired ORDER BY canonical_id"
            ).fetchall()
        ]

    def put_group_ownership(self, code: str, campaign_id: str,
                            business_unit: str, kind: str) -> None:
        current = self.db.execute(
            "SELECT campaign_id,business_unit,kind FROM group_ownership "
            "WHERE code=?", (code,)
        ).fetchone()
        # A VICIdial user group is intentionally shared by every campaign in
        # one business unit.  Preserve its first campaign as provenance, while
        # rejecting cross-business-unit or cross-kind reuse.
        if current and (
            current["business_unit"] != business_unit or current["kind"] != kind
        ):
            raise ValueError("group ownership collision")
        self.db.execute(
            "INSERT OR IGNORE INTO group_ownership VALUES(?,?,?,?)",
            (code, campaign_id, business_unit, kind),
        )
        self.db.commit()

    def group_owner(self, code: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT campaign_id,business_unit,kind FROM group_ownership "
            "WHERE code=?", (code,)
        ).fetchone()

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
