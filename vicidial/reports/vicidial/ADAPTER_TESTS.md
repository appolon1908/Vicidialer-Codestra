# Adapter tests

Latest isolated review-correction run: **29 passed** using Python 3.11. All
existing tests remain included.

Covered:

- route contract and absence of SQL/shell/exec/file routes
- source, identity, scope, signature, timestamp, nonce, and replay rejection
- strict schema and business-unit validation
- disabled-by-default provisioning and read-back
- idempotent retry and conflict handling
- compatible adoption and incompatible collision blocking
- pre-mutation rollback safety and snapshots
- kill switches
- agent sync/disable
- lead duplicate retry
- callback read-back
- same-campaign transfer allowlist
- reconciliation
- raw-byte scanner whitespace/newline semantics with synthetic-only fixtures
- additive `allowed_campaigns` adoption and unrelated campaign preservation
- malformed, duplicate, unrestricted, and incompatible ownership rejection
- complete per-resource desired-versus-actual read-back
- group, script, disposition, transfer-policy, and disabled-state mismatch
- redacted evidence, preserved snapshots, partial-failure retry, and
  reconciliation after correction

## Staging deployment

The separate `codestra-vicidial-restricted-adapter.service` passes:

- `systemd-analyze verify`
- systemd security exposure score 1.5 (`OK`)
- Apache syntax validation
- firewalld configuration validation
- loopback `/health`, `/ready`, and `/version`
- adapter-only graceful restart
- periodic readiness service
- all negative tests in `ADAPTER_NEGATIVE_TESTS.md`

Positive mTLS health originating on `10.40.0.1` remains **BLOCKED** pending an
authenticated execution channel on Application Server A.

No live VICIdial/MariaDB mutation or customer destination was used.
