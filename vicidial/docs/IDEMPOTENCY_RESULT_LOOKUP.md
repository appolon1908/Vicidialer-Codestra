# Recover a committed adapter command result

Middleware can use `POST /v1/idempotency/lookup` after losing the response to an
adapter mutation. The API retrieves the historical committed result from the
adapter's durable SQLite command ledger. It does not dispatch a command, access
telephony state, release a pending claim, advance a resource version, or require
write/dialing flags to be enabled.

## Request and authentication

Use the normal private adapter endpoint and version 2 signed-request headers
with the `telephony:read` scope. Send these JSON fields:

| Field | Value |
| --- | --- |
| `operation` | One of the supported operation names below |
| `idempotency_key` | The original mutation's idempotency key |
| `command` | The complete original mutation body, including its original context |

The lookup has no `Idempotency-Key` header. Its HMAC canonical input uses an
empty header idempotency key; the original key in the body is covered by the
body digest. Use a fresh authentication timestamp, nonce and signature for each
lookup. Preserve the original command's correlation ID, requested time and
expected version. The adapter validates and normalizes the original command
with the same model used by the mutation before comparing its digest.

The original tenant, business unit and supervisor must still hold registered
authority for the campaign. A revoked or mismatched authority returns 403.

| Operation | Original mutation route |
| --- | --- |
| `campaign.provision-disabled` | `POST /v1/campaigns/provision-disabled` |
| `campaign.disable` | `POST /v1/campaigns/disable` |
| `agent.sync` | `POST /v1/agents/sync` |
| `agent.provision-disabled` | `POST /v1/agents/provision-disabled` |
| `agent.disable` | `POST /v1/agents/disable` |
| `lead.publish` | `POST /v1/leads/publish` |
| `callback.create` | `POST /v1/callbacks/create` |
| `transfer-policy.sync` | `POST /v1/transfer-policies/sync` |

OpenAPI publishes a discriminated union of these request bodies and the typed
lookup response. Call origination and reconciliation jobs use separate
operation records and are outside this lookup contract.

## Interpret the response

A 200 response contains `status: recorded`, `operation`, `idempotency_key`,
a timezone-aware `recorded_at`, and the saved `result`. The result includes
the resource version recorded on completion. It excludes the transient
`idempotent_replay` flag added by the mutation response.

This is a historical outcome. Another command may have changed the resource
since it was recorded. Use actual-state/readback or reconciliation before
submitting a new mutation with the current resource version.

| HTTP status | Meaning | Middleware action |
| --- | --- | --- |
| 200 | Matching committed result found | Recover the saved outcome; reconcile current state as needed |
| 403 | Signature, scope or campaign authority denied | Correct the authorized identity/context |
| 404 | No committed result for this operation and key | Keep the outcome unknown and reconcile before any retry |
| 409 | Key exists with a different normalized command | Recover the original body; do not substitute a new key |
| 422 | Unsupported operation or invalid command | Correct the request contract |
| 503 | Durable result or lookup audit could not be verified | Preserve the unknown outcome and reconcile |

**A 404 does not prove that the mutation never executed.** The original command
may still be in progress or may have changed telephony state before its durable
completion failed. Lookup leaves any pending claim intact. Existing
mutation/readback gates continue to block conflicting writes.

The ledger is opened using a separate read-only connection. Uncommitted
records are not visible, missing databases are not created, and malformed
results return a redacted reconciliation error.

## Lookup audit trail

Every lookup outcome appends an `idempotency.lookup` audit event, including
success, missing results, conflicts, denied authority and malformed requests.
The event records correlation, authenticated service identity, outcome, and
hashed target/status details. It never stores the command, recovered result,
raw idempotency key or authentication headers. Requests that fail authentication
use a generated correlation ID and an `unauthenticated` actor.

Audit uses a separate write transaction so it cannot commit or release a pending
mutation on the shared connection. If the audit sink is unavailable, the adapter
emits a redacted service-log event and returns 503; it withholds an otherwise
successful result. A missing database is not recreated for auditing.

## Verification boundary

`vicidial/tests/test_command_lookup.py` exercises signed HTTP requests with
synthetic repositories and real temporary SQLite databases, including process
state recreation, historical versions, authority changes, uncertain completion,
transaction isolation and malformed ledger records. Container and MariaDB
release checks remain separate gates. This API change does not certify a
Server B release or authorize activation of dialing.
