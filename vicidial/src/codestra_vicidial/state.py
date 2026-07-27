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
        CREATE TABLE IF NOT EXISTS transfer_policies(
          campaign_id TEXT PRIMARY KEY, targets TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots(
          snapshot_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
          target_hash TEXT NOT NULL, state_json TEXT NOT NULL, created_at TEXT NOT NULL);
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
