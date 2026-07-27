# Adapter tests

Latest isolated review-correction run: **32 passed in 2.90 seconds** using
Python 3.11. All original 13 tests remain included.

Authentication/source/scope/signature/replay rejection, strict schemas,
absence of SQL/shell/file endpoints, kill switches, idempotency, adoption,
collision rejection, disabled read-back, snapshotting, agent/lead/callback
flows, transfer policy, reconciliation, rollback safety, scanner regex
semantics, additive user-group membership, full per-resource read-back,
redacted mismatch evidence, partial-failure retry, and correction
reconciliation passed.

The deployment unit is a template and is not installed; `systemd-analyze
verify` therefore reports the expected missing `/opt/.../uvicorn` executable.
No service was started.

## Baseline suite

Command: `/root/codestra-vicidial-edge/.test-venv/bin/pytest -q`

Result: `72 passed, 3 skipped, 44 subtests passed in 6.99s`.

## New restricted adapter suite

Command:

```text
PYTHONPATH=src /root/codestra-vicidial-edge/.test-venv/bin/pytest -q
```

Result: `32 passed in 2.90s`.

Covered: route contract; source, identity, scope, signature, timestamp/nonce
authentication; replay rejection; strict schema validation; disabled-by-default
provisioning; read-back; idempotent retry; compatible adoption; incompatible
collision; kill switch; agent disable; lead duplicate retry; callback read-back;
same-campaign transfer allowlist; disable; reconciliation; scanner raw-byte
regex semantics; additive `allowed_campaigns` handling; malformed membership
rejection; unrelated campaign preservation; group/script/disposition/transfer
and disabled-state mismatches; snapshot/evidence preservation; and retry after
correction.

No live mutation or customer destination was used.
