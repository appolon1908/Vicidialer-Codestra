# Adapter tests

Latest isolated run: **13 passed in 1.04 seconds** using Python 3.11.

Authentication/source/scope/signature/replay rejection, strict schemas,
absence of SQL/shell/file endpoints, kill switches, idempotency, adoption,
collision rejection, disabled read-back, snapshotting, agent/lead/callback
flows, transfer policy, reconciliation, and rollback safety passed.

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

Result: `12 passed in 0.98s`.

Covered: route contract; source, identity, scope, signature, timestamp/nonce
authentication; replay rejection; strict schema validation; disabled-by-default
provisioning; read-back; idempotent retry; compatible adoption; incompatible
collision; kill switch; agent disable; lead duplicate retry; callback read-back;
same-campaign transfer allowlist; disable; reconciliation.

No live mutation or customer destination was used.
