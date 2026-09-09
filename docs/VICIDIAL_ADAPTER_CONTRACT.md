# Restricted VICIdial Adapter Contract

## Network ownership

- VICIdial server: `65.21.67.207`
- Authorized application server: `65.109.65.169`
- The adapter must accept operational requests only from the authorized application server or an approved private network/VPN.
- Prefer mTLS or an existing trusted private channel.
- If the adapter must bind to a public interface, firewall it to the exact authorized source and require application-layer authentication.
- Do not expose VICIdial MariaDB to the application server or the public Internet.

## Security

- No general SQL endpoint.
- No shell-command endpoint.
- No arbitrary file-write endpoint.
- Every command is schema validated.
- Every mutation is idempotent.
- Every request has correlation ID, actor, reason, timestamp, and signature/token.
- All secrets are stored outside Git.
- Logs redact credentials and customer data.
- Adapter service runs under a dedicated least-privilege account when compatible with the existing VICIdial installation.
- Changes are backed up and read back.

## Required adapter API families

```text
GET  /health
GET  /ready
GET  /version

POST /v1/campaigns/validate
POST /v1/campaigns/provision-disabled
POST /v1/campaigns/disable
GET  /v1/campaigns/{campaign_id}/actual-state

POST /v1/agents/sync
GET  /v1/agents/availability/{user_id}/{extension}
POST /v1/agents/provision-disabled
POST /v1/agents/disable
GET  /v1/agents/{user_id}/actual-state

POST /v1/leads/publish
GET  /v1/leads/by-external-id/{external_id}

POST /v1/callbacks/create
GET  /v1/callbacks/{callback_id}

POST /v1/transfer-policies/sync

POST /v1/reconciliation/run
GET  /v1/reconciliation/{run_id}
```

The bounded disabled-agent profile is limited to the exact policy-authorized
identity. For the Appolon synthetic supervisor request this is `appolon`, extension
`6901`, group `WEBTEST_SYN`, campaign `TEST_SYN`, VICIdial level 8, `active=N`,
internal-only, and external dialing disabled. Availability returns only booleans and
existing non-secret identifiers. Credential material comes from the protected local
secret-authority socket and is never returned or audited. User and phone creation are
one transaction; any collision or partial failure rolls back both records.

Campaign provisioning validates and creates its scripts and dispositions as one
complete, disabled desired-state operation. Separate broad sync aliases are not
exposed because they misleadingly suggested narrower mutations.

## Local implementation strategy

The adapter may use controlled local database operations when VICIdial has no sufficient supported administrative API.

Requirements:

- Local connection only.
- Parameterized queries.
- Explicit allowlisted tables and columns.
- Precondition checks.
- Transactions when safe.
- Compensating rollback when multiple systems/tables are involved.
- No destructive schema changes.
- No deletion of historical records.
- Read-back after mutation.
- Evidence for every applied resource.
- Compatibility with current VICIdial/Asterisk behavior.

## Production defaults

All newly created campaigns, lists, groups, scripts, and workflows are disabled or non-dialing by default.

The adapter must refuse an activation command unless the middleware sends evidence references for the required gates and the activation feature flag is enabled.

## Middleware request and concurrency contract

`/openapi.json` includes the exact JSON command schemas for every body-bearing
command, including tenant, business-unit, campaign, supervisor and
`expected_version` requirements. HMAC v2 still signs the exact transmitted bytes;
command parsing follows authentication. Campaign validation uses the same error
handling as provisioning and returns 403 for denied authority and 422 for malformed
commands. Command validation errors identify field locations and error types,
without echoing submitted values.

A stale `expected_version` returns HTTP 409 with `code=stale_resource_version`.
An outstanding durable resource claim returns HTTP 409 with
`code=resource_mutation_in_progress`. Neither rejection applies a new mutation.
Clients should reconcile a stale version before submitting a revised command with
a new idempotency key. For an ambiguous response, retry the identical command and
key with a fresh timestamp, nonce and signature; a recorded successful result
retains its original resource version and reports `idempotent_replay=true`.
A pending claim requires reconciliation; clients must not clear claims themselves.

Campaign and agent actual-state reads and lead external-ID lookups include
`resource_version`, the adapter's committed command version. Campaigns without a
durable ownership mapping return null instead of an invented managed version.
Versions cover adapter commands, not out-of-band VICIdial edits. Read-back does not
reserve a version: a subsequent concurrent write can still cause a 409.

### Durable command completion and callback recovery

After a mutation passes read-back, the adapter commits the resource version,
idempotency response and success audit in one SQLite transaction on a dedicated
connection. A completion error rolls all three back and retains the resource claim.
The API returns HTTP 503 with `code=mutation_completion_uncertain` and
`status=reconciliation_required`; subsequent writes to that resource remain blocked
by the claim. This transaction covers adapter metadata only: it cannot roll back
an already applied VICIdial mutation. No automatic claim expiry or deletion is
performed. Reconciliation must verify both stores before controlled recovery.

An exact replay survives an adapter restart. If another worker completes the exact
command between replay lookup and version claiming, the adapter returns the recorded
response. Reusing a completed key with a different payload returns HTTP 409 with
`code=idempotency_conflict`; callers must not change the payload under the old key.
Resource collisions also return 409 instead of being converted to validation errors.

`GET /v1/callbacks/by-external-id/{external_id}` requires signed `telephony:read`
authority. It resolves the durable mapping, reads VICIdial by callback ID, verifies
the external reference, and returns a typed response containing `external_id`,
`callback_id`, `resource_version`, `campaign_id`, `lead_id`, `user_id`, `recipient`,
`status`, and UTC `callback_time`. Internal comments/ownership markers are omitted.
The status is current read-back, while the version is the committed adapter version.
An unknown mapping returns 404; missing, conflicting or invalid actual state returns
409 with `status=reconciliation_required` instead of stale mapping data.
