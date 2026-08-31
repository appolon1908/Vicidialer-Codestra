# Runtime Safety Review Evidence — 2026-08-31

## Scope

This evidence records the forward corrections made after exact-head review of the dedicated VICIdial R3 foundation. It is source evidence only and does not authorize deployment, dialing, callbacks, credentials, campaign activation, database mutation, or customer traffic.

## Corrected source parent

```text
CORRECTED_SOURCE_PARENT=cc39f5f1d387e67b73c7092255c1efea8d756b95
```

## Review corrections

### Durable Middleware event acceptance

A generic HTTP `2xx` is no longer sufficient to mark a VICIdial event as sent. The canonical event client now requires a bounded JSON acknowledgement proving:

```text
persisted=true
status=persisted
event_id=<exact submitted event ID>
idempotency_key=<exact submitted idempotency key>
```

Empty, malformed, non-durable, mismatched-event, or mismatched-idempotency acknowledgements fail closed. The candidate/reference source is kept byte-identical.

### Operational repository readiness

The readiness probe now exercises the exact repository connection used by operational requests. It performs reconnect-aware ping, verifies the selected `asterisk` database, and verifies the required VICIdial tables. A separate throwaway connection can no longer make a stale operational connection appear ready.

### Complete production backlog enforcement

The synchronization worker now counts the complete eligible production event backlog before selecting a bounded processing batch. The hard backlog limit therefore remains enforceable even when `batch_size` is lower than that limit. A batch query can no longer hide an oversized queue.

## Focused regression evidence

The one-use repair workflow applied the patch and passed:

```text
FOCUSED_TESTS=50_PASSED
PYTHON_COMPILATION=PASS
CANONICAL_EVENT_CLIENT_REFERENCE_MATCH=PASS
VICIDIAL_SYNC_CANDIDATE_MATCH=PASS
GIT_DIFF_CHECK=PASS
```

The one-use repair script, trigger, and workflow removed themselves before the corrected source commit was created.

## Permanent safety state

```text
CALLS_PLACED=0
PRODUCTION_DIALING=false
CALLBACK_DISPATCH=false
TELEPHONY_ADMIN_WRITE=false
RECORDING_WRITE=false
LEAD_PUBLICATION=false
CUSTOMER_TRAFFIC=NOT_AUTHORIZED
DEPLOYMENT_AUTHORIZED=false
PRODUCTION_AUTHORIZED=false
SERVER_CHANGED=NO
```

Full exact-head, synthetic merge-result, container, OCI, SBOM, provenance, vulnerability, and secret certification must pass again on the final user-authored source head before protected promotion.
