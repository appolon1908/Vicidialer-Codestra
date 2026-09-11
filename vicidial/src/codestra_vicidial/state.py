from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class StaleResourceVersion(ValueError):
    """The caller must reconcile its version before submitting a new command."""


class ResourceMutationInProgress(RuntimeError):
    """A durable claim prevents overlapping writes to the same resource."""


class IdempotencyConflict(ValueError):
    """A completed command key cannot be reused with a different payload."""


class MutationCompletionUncertain(RuntimeError):
    """Persistence after a verified mutation requires operator reconciliation."""


class CommandRecordUnavailable(RuntimeError):
    """A durable command record could not be read and validated."""


class CommandAuditUnavailable(RuntimeError):
    """A lookup audit event could not be persisted."""


class ResourceSnapshotChanged(RuntimeError):
    """Actual state cannot be paired with a version across a concurrent write."""


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._transaction_lock = threading.RLock()
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
        CREATE TABLE IF NOT EXISTS campaign_authority(
          campaign_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
          business_unit TEXT NOT NULL, supervisor_subject TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS resource_versions(
          resource_key TEXT PRIMARY KEY, version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS resource_epochs(
          resource_key TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS resource_mutations(
          resource_key TEXT PRIMARY KEY, expected_version INTEGER NOT NULL,
          idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_desired(
          user_id TEXT PRIMARY KEY, desired_json TEXT NOT NULL,
          desired_hash TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_adoption_intents(
          user_id TEXT PRIMARY KEY, original_hash TEXT NOT NULL,
          desired_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS webrtc_sessions(
          user_id TEXT PRIMARY KEY, extension TEXT NOT NULL,
          credential_reference TEXT NOT NULL, single_use_consumed INTEGER NOT NULL DEFAULT 0,
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reconciliation_runs(
          run_id TEXT PRIMARY KEY, result_json TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(
          evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
          operation TEXT NOT NULL, target_hash TEXT NOT NULL, outcome TEXT NOT NULL,
          redacted_json TEXT NOT NULL);
        """)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS auth_nonces(
          nonce TEXT PRIMARY KEY,
          expires_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS auth_nonces_expiry
          ON auth_nonces(expires_at);

        CREATE TABLE IF NOT EXISTS auth_rate_events(
          source TEXT NOT NULL,
          occurred_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS auth_rate_events_source_time
          ON auth_rate_events(source, occurred_at);
        """)
        self.db.commit()

    def resource_version(self, resource_key: str) -> int:
        row = self.db.execute(
            "SELECT version FROM resource_versions WHERE resource_key=?",
            (resource_key,),
        ).fetchone()
        return int(row["version"]) if row else 0

    def resource_observation(self, resource_key: str) -> tuple[int, int, bool]:
        # One SQLite statement observes all three fields from one committed
        # snapshot, independent of transactions on the shared connection.
        db = sqlite3.connect(self.path)
        try:
            version, epoch, pending = db.execute(
                "SELECT COALESCE((SELECT version FROM resource_versions WHERE resource_key=?),0),"
                "COALESCE((SELECT epoch FROM resource_epochs WHERE resource_key=?),0),"
                "EXISTS(SELECT 1 FROM resource_mutations WHERE resource_key=?)",
                (resource_key, resource_key, resource_key),
            ).fetchone()
            return int(version), int(epoch), bool(pending)
        finally:
            db.close()

    def claim_resource_version(
        self, resource_key: str, expected: int, idempotency_key: str
    ) -> None:
        with self._transaction_lock:
            self._claim_resource_version(resource_key, expected, idempotency_key)

    def _claim_resource_version(
        self, resource_key: str, expected: int, idempotency_key: str
    ) -> None:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.resource_version(resource_key)
            if current != expected:
                raise StaleResourceVersion("expected version is stale")
            pending = self.db.execute(
                "SELECT 1 FROM resource_mutations WHERE resource_key=?",
                (resource_key,),
            ).fetchone()
            if pending:
                raise ResourceMutationInProgress("resource mutation is already in progress")
            self.db.execute(
                "INSERT INTO resource_mutations VALUES(?,?,?,?)",
                (resource_key, expected, idempotency_key,
                 datetime.now(timezone.utc).isoformat()),
            )
            self.db.execute(
                "INSERT INTO resource_epochs VALUES(?,1) ON CONFLICT(resource_key) "
                "DO UPDATE SET epoch=epoch+1", (resource_key,),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def complete_resource_version(
        self, resource_key: str, expected: int, idempotency_key: str
    ) -> int:
        with self._transaction_lock:
            return self._complete_resource_version(
                resource_key, expected, idempotency_key
            )

    def _complete_resource_version(
        self, resource_key: str, expected: int, idempotency_key: str
    ) -> int:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            claim = self.db.execute(
                "SELECT expected_version,idempotency_key FROM resource_mutations "
                "WHERE resource_key=?", (resource_key,),
            ).fetchone()
            if (
                claim is None
                or int(claim["expected_version"]) != expected
                or claim["idempotency_key"] != idempotency_key
                or self.resource_version(resource_key) != expected
            ):
                raise RuntimeError("resource mutation claim was lost")
            updated = expected + 1
            self.db.execute(
                "INSERT INTO resource_versions(resource_key,version) VALUES(?,?) "
                "ON CONFLICT(resource_key) DO UPDATE SET version=excluded.version",
                (resource_key, updated),
            )
            self.db.execute(
                "DELETE FROM resource_mutations WHERE resource_key=?",
                (resource_key,),
            )
            self.db.commit()
            return updated
        except Exception:
            self.db.rollback()
            raise

    def release_resource_claim(
        self, resource_key: str, expected: int, idempotency_key: str
    ) -> None:
        with self._transaction_lock:
            self.db.execute(
                "DELETE FROM resource_mutations WHERE resource_key=? "
                "AND expected_version=? AND idempotency_key=?",
                (resource_key, expected, idempotency_key),
            )
            self.db.commit()

    def complete_command(
        self, resource_key: str, expected: int, key: str, operation: str,
        request_hash: str, result: dict, correlation_id: str, actor: str,
        target: str,
    ) -> dict:
        """Commit the version, replay result and audit as one durable unit.

        Use a dedicated connection so unrelated users of the shared connection
        cannot accidentally commit part of this transaction.
        """
        completed = result | {"resource_version": expected + 1}
        encoded = json.dumps(completed, sort_keys=True)
        occurred_at = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            claim = db.execute(
                "SELECT expected_version,idempotency_key FROM resource_mutations "
                "WHERE resource_key=?", (resource_key,),
            ).fetchone()
            version = db.execute(
                "SELECT version FROM resource_versions WHERE resource_key=?",
                (resource_key,),
            ).fetchone()
            if claim != (expected, key) or (version[0] if version else 0) != expected:
                raise RuntimeError("resource mutation claim was lost")
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?)",
                (operation, key, request_hash, encoded, occurred_at),
            )
            db.execute(
                "INSERT INTO resource_versions VALUES(?,?) "
                "ON CONFLICT(resource_key) DO UPDATE SET version=excluded.version",
                (resource_key, expected + 1),
            )
            db.execute(
                "INSERT INTO audit(occurred_at,correlation_id,actor,operation,"
                "target_hash,outcome,detail_hash) VALUES(?,?,?,?,?,?,?)",
                (occurred_at, correlation_id, actor, operation, self.digest(target),
                 "success", self.digest(completed)),
            )
            db.execute("DELETE FROM resource_mutations WHERE resource_key=?", (resource_key,))
            db.commit()
            return completed
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def put_campaign_authority(
        self, campaign_id: str, tenant_id: str, business_unit: str,
        supervisor_subject: str,
    ) -> None:
        current = self.campaign_authority(campaign_id)
        desired = (tenant_id, business_unit, supervisor_subject)
        if current and tuple(current[key] for key in (
            "tenant_id", "business_unit", "supervisor_subject"
        )) != desired:
            raise ValueError("campaign authority collision")
        self.db.execute(
            "INSERT OR IGNORE INTO campaign_authority VALUES(?,?,?,?)",
            (campaign_id, *desired),
        )
        self.db.commit()

    def campaign_authority(self, campaign_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT tenant_id,business_unit,supervisor_subject "
            "FROM campaign_authority WHERE campaign_id=?", (campaign_id,)
        ).fetchone()

    def consume_nonce(
        self,
        nonce: str,
        current: int,
        expiry: int,
    ) -> bool:
        try:
            self.db.execute("BEGIN IMMEDIATE")

            self.db.execute(
                "DELETE FROM auth_nonces "
                "WHERE expires_at < ?",
                (current,),
            )

            try:
                self.db.execute(
                    "INSERT INTO auth_nonces("
                    "nonce,expires_at"
                    ") VALUES(?,?)",
                    (nonce, expiry),
                )

            except sqlite3.IntegrityError:
                self.db.rollback()
                return False

            self.db.commit()
            return True

        except Exception:
            self.db.rollback()
            raise

    def consume_rate_limit(
        self,
        source: str,
        current: int,
        window_seconds: int,
        request_limit: int,
    ) -> bool:
        cutoff = current - window_seconds

        try:
            self.db.execute("BEGIN IMMEDIATE")

            self.db.execute(
                "DELETE FROM auth_rate_events "
                "WHERE occurred_at <= ?",
                (cutoff,),
            )

            row = self.db.execute(
                "SELECT COUNT(*) AS count "
                "FROM auth_rate_events "
                "WHERE source=? AND occurred_at>?",
                (source, cutoff),
            ).fetchone()

            if int(row["count"]) >= request_limit:
                self.db.rollback()
                return False

            self.db.execute(
                "INSERT INTO auth_rate_events("
                "source,occurred_at"
                ") VALUES(?,?)",
                (source, current),
            )

            self.db.commit()
            return True

        except Exception:
            self.db.rollback()
            raise

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
            raise IdempotencyConflict("idempotency conflict")
        return json.loads(row["result"])

    def lookup_command(self, operation: str, key: str, request_hash: str) -> dict | None:
        """Read one committed record without creating or repairing any state."""
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT request_hash,result,created_at FROM requests "
                "WHERE operation=? AND idempotency_key=?", (operation, key),
            ).fetchone()
            if row is None:
                return None
            if row[0] != request_hash:
                raise IdempotencyConflict("idempotency conflict")
            result = json.loads(row[1])
            if not isinstance(result, dict):
                raise ValueError("invalid command record")
            json.dumps(result, allow_nan=False)
            return {"recorded_at": row[2], "result": result}
        except IdempotencyConflict:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            raise CommandRecordUnavailable("durable command result unavailable") from exc
        finally:
            if db is not None:
                db.close()


    def audit_lookup(
        self, correlation_id: str, actor: str, target: object,
        outcome: str, http_status: int,
    ) -> None:
        """Append a redacted event without committing a shared mutation."""
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2)
            db.execute(
                "INSERT INTO audit(occurred_at,correlation_id,actor,operation,"
                "target_hash,outcome,detail_hash) VALUES(?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(), correlation_id, actor,
                    "idempotency.lookup", self.digest(target), outcome,
                    self.digest({"http_status": http_status}),
                ),
            )
            db.commit()
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise CommandAuditUnavailable("lookup audit unavailable") from exc
        finally:
            if db is not None:
                db.close()

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

    def put_agent_mapping(
        self, external_id: str, actual_id: str, payload_hash: str
    ) -> None:
        """Update agent desired state while preserving stable ownership."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.mapping("agent", external_id)
            if current is not None and current["actual_id"] != actual_id:
                raise ValueError("ownership collision: agent mapping changed")
            claimed = self.mapping_for_actual("agent", actual_id)
            if claimed is not None and claimed["external_id"] != external_id:
                raise ValueError("ownership collision: agent already claimed")
            self.db.execute(
                "INSERT INTO mappings(kind,external_id,actual_id,payload_hash) "
                "VALUES('agent',?,?,?) ON CONFLICT(kind,external_id) DO UPDATE SET "
                "payload_hash=excluded.payload_hash",
                (external_id, actual_id, payload_hash),
            )
            self.db.commit()
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

    def put_agent_desired(self, user_id: str, desired: dict) -> None:
        payload_hash = self.digest(desired)
        self.db.execute(
            "INSERT INTO agent_desired VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET desired_json=excluded.desired_json,"
            "desired_hash=excluded.desired_hash,updated_at=excluded.updated_at",
            (user_id, json.dumps(desired, sort_keys=True), payload_hash,
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def agent_desired(self, user_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT desired_json,desired_hash FROM agent_desired WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return (
            {"desired": json.loads(row["desired_json"]),
             "desired_hash": row["desired_hash"]}
            if row else None
        )

    def put_agent_adoption_intent(
        self, user_id: str, original_hash: str, desired_hash: str
    ) -> None:
        """Persist retry authority before a non-transactional MyISAM mutation."""
        self.db.execute(
            "INSERT INTO agent_adoption_intents VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (user_id, original_hash, desired_hash,
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def agent_adoption_intent(self, user_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT original_hash,desired_hash FROM agent_adoption_intents "
            "WHERE user_id=?", (user_id,)
        ).fetchone()

    def clear_agent_adoption_intent(self, user_id: str) -> None:
        self.db.execute(
            "DELETE FROM agent_adoption_intents WHERE user_id=?", (user_id,)
        )
        self.db.commit()

    def finalize_agent_adoption(
        self, user_id: str, actual_id: str, desired_hash: str, desired: dict
    ) -> None:
        """Atomically publish ownership, desired state, and intent completion."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            intent = self.db.execute(
                "SELECT desired_hash FROM agent_adoption_intents WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if intent is None or intent["desired_hash"] != desired_hash:
                raise ValueError("agent adoption intent missing or changed")
            self.db.execute(
                "INSERT INTO mappings(kind,external_id,actual_id,payload_hash) "
                "VALUES('agent',?,?,?) ON CONFLICT(kind,external_id) DO UPDATE SET "
                "payload_hash=excluded.payload_hash",
                (user_id, actual_id, desired_hash),
            )
            self.db.execute(
                "INSERT INTO agent_desired VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET desired_json=excluded.desired_json,"
                "desired_hash=excluded.desired_hash,updated_at=excluded.updated_at",
                (user_id, json.dumps(desired, sort_keys=True), desired_hash,
                 datetime.now(timezone.utc).isoformat()),
            )
            self.db.execute(
                "DELETE FROM agent_adoption_intents WHERE user_id=?", (user_id,)
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def put_webrtc_session(
        self, user_id: str, extension: str, credential_reference: str, *, expires_at: str,
    ) -> None:
        """Record the one active WebRTC registration credential for a user.

        Mission 6/7 requires at most one active WebRTC endpoint per user:
        the primary key on ``user_id`` means a second ``provision_webrtc``
        call for the same user replaces (rather than adds to) this row -
        the caller (AdapterService.provision_webrtc) is responsible for
        rejecting a second *concurrent* registration attempt with
        ``WEBRTC_SESSION_ALREADY_ACTIVE`` before it ever reaches here.
        """
        self.db.execute(
            "INSERT INTO webrtc_sessions(user_id,extension,credential_reference,"
            "single_use_consumed,expires_at,created_at) VALUES(?,?,?,0,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET extension=excluded.extension,"
            "credential_reference=excluded.credential_reference,single_use_consumed=0,"
            "expires_at=excluded.expires_at,created_at=excluded.created_at",
            (user_id, extension, credential_reference, expires_at,
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def webrtc_session(self, user_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT extension,credential_reference,single_use_consumed,expires_at "
            "FROM webrtc_sessions WHERE user_id=?", (user_id,),
        ).fetchone()

    def consume_webrtc_session(self, user_id: str) -> None:
        """Mark the credential used - a single-use ticket, per the spec's
        'never return a long-lived SIP password' rule; a second attempt to
        register with the same ticket must be rejected by the caller."""
        self.db.execute(
            "UPDATE webrtc_sessions SET single_use_consumed=1 WHERE user_id=?",
            (user_id,),
        )
        self.db.commit()

    def clear_webrtc_session(self, user_id: str) -> None:
        self.db.execute("DELETE FROM webrtc_sessions WHERE user_id=?", (user_id,))
        self.db.commit()

    def persist_reconciliation_run(
        self,
        run_id: str,
        result: dict,
        idempotency_key: str,
        request_hash: str,
    ) -> dict | None:
        """Atomically persist the run and its replay authority."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            prior = self.db.execute(
                "SELECT request_hash,result FROM requests "
                "WHERE operation=? AND idempotency_key=?",
                ("reconciliation.run", idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise ValueError("idempotency conflict")
                self.db.commit()
                return json.loads(prior["result"])
            created_at = datetime.now(timezone.utc).isoformat()
            self.db.execute(
                "INSERT INTO reconciliation_runs VALUES(?,?,?)",
                (run_id, json.dumps(result, sort_keys=True), created_at),
            )
            response = {"run_id": run_id, **result}
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?)",
                (
                    "reconciliation.run", idempotency_key, request_hash,
                    json.dumps(response, sort_keys=True), created_at,
                ),
            )
            self.db.commit()
            return None
        except Exception:
            self.db.rollback()
            raise

    def reconciliation_run(self, run_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT result_json,created_at FROM reconciliation_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return (
            {"run_id": run_id, "created_at": row["created_at"],
             **json.loads(row["result_json"])}
            if row else None
        )

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
