# Issue #11: executable client integration and remaining bindings

## Corrected staging integration

The shipped staging lifecycle previously sent commands without tenant, business
unit, campaign, supervisor and expected-version context. Its fake-only test
accepted those bodies, while the current signed adapter API rejected the first
provisioning request with HTTP 422. It also reused version zero when disabling
the newly provisioned campaign.

The client now supplies explicit tenant/supervisor authority, verifies agent
campaign membership through actual-state read-back, and binds disable to a fresh
campaign version. The original provisioning body remains unchanged for exact-key
replay. Missing dispositions, missing versions, concurrent writes and mismatched
agent membership block completion.

This is an isolated staging acceptance flow. It requires a TEST outbound design
and a dedicated synthetic adapter/database. It must not run against Server B's
production database or use customer identities. Its Odoo/n8n projections are
local SQLite test records; the result explicitly identifies that limitation.

## Durable response recovery

`campaign_provisioning.PrivateAdapterClient.lookup_command(operation, key, command)`
calls the merged `POST /v1/idempotency/lookup` contract with HMAC v2,
`telephony:read`, a fresh nonce, and no mutation `Idempotency-Key` header.
It validates the returned operation, original key, timestamp and committed
resource version. HTTP, transport, malformed-response and size errors are
redacted `AdapterRequestError` exceptions with `status_code` when available.

`LifecycleStore.remember_command` commits each original synthetic mutation body
before dispatch. The same key cannot overwrite a different body.
`LifecycleStore.original_command` verifies its stored digest and recovers the
original body after restart. The ledger must reside in an access-restricted
directory; these are synthetic command records, not public evidence.

An explicit recovery sequence is:

```python
original = store.original_command(operation, original_key)
receipt = client.lookup_command(operation, original_key, original)
historical_result = receipt["result"]
```

A missing result, denied request, conflict or unavailable audit does not prove
non-execution. Preserve the unknown outcome and reconcile; do not issue another
mutation, change the key, clear a resource claim or rerun the whole lifecycle.
An existing synthetic run now stops before dispatching new remote commands.
Read current actual state before constructing any subsequent versioned command.

No automatic mutation retry is added. The result receipt is historical, and a
later command may already have advanced the resource version.

## Cross-repository code still required

Source inspection used VICIdial protected main
`573eab2c05ed5bbc7f119200466b83545cc2c2a5` and Middleware main
`0f2144b9734252da6ef7fe3b1209302f6e60defd`.

| Middleware source | Current boundary | Required integration |
| --- | --- | --- |
| `app/adapters/telephony/client.py` | Sends `TelephonyCommandRequest`, expects an asynchronous `operation_id` acknowledgement and registry read-back | Add an explicit restricted-adapter binding that sends typed campaign/agent commands, preserves original command intents and handles durable result receipts |
| `app/core/telephony_commands.py` | Uses public resource IDs, committed allocation references and logical endpoint keys | Resolve approved tenant/campaign/supervisor and resource/version mappings; do not infer internal telephony identities from public IDs |
| `app/adapters/vicidial/mtls_client.py` | Allows existing transfer and originate routes | Wire the restricted API's HMAC v2 and least-privilege scopes through its separately approved private route; preserve the existing call/transfer boundary |
| `connectors/manifests/vicidial-restricted.connector.json` | Disabled template, unverified URL, OAuth/mTLS and `/v1/operations/{operation_id}/status` | Publish a reviewed runtime binding matching the actual adapter contract, certificate identity and endpoint registry |
| Odoo projection and n8n workers | Separate authenticated asynchronous consumers | Certify actual persisted delivery/read-back using governed isolated identities; local staging projection rows do not establish delivery |

Generic phone/endpoint apply operations cannot be aliased to the current
`agent.provision-disabled` API: that API has a deliberately narrow,
policy-authorized identity. A broader provisioner requires its own reviewed
capability, identity/allocation mapping, backup, idempotency and read-back design.
Do not add a generic SQL, shell or AMI proxy to accommodate the other envelope.

## Observed runtime and release state

Read-only Server B observation at 2026-09-10T17:13:08Z:

- Restricted adapter, passive gateway and Asterisk were active.
- `/health`, `/ready` and `/version` returned 200.
- Active source was `86b724819919d5c021c52d27baf27e0f14310fd5`,
  profile `staging-read-only`, backend `memory`.
- Writes, internal test calling, production dialing and customer traffic were false.
- Readiness does not certify the operational MariaDB-backed candidate.

The existing protected source publication for merged `573eab2` completed
successfully in [run 34506362232](https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/34506362232).
Publication alone does not verify the installed release or satisfy recovery gates.

The reviewed prepared-release controller is merged, but its governed installation,
release-local dependency preparation, configuration/state compatibility and
independently verified backup/off-host/isolated-restore reports must be established
before its activation transaction. At 2026-09-10T17:29:03Z, the fixed
`plan.json`, `verified-release.json` and `verified-backup.json` files were absent
under `/etc/codestra/vicidial-release/`; the installed operator still matched
the previously observed no-op source hash. Configuration/event reconciliation and the
governed call canary with durable CDR/disposition/duration/hangup read-back remain
issue #11 runtime gates. This code change does not change services, grants,
outbox eligibility, event delivery, production dialing or live call quotas.

## Test boundary

`tests/test_synthetic_campaign_http.py` runs the shipped private client through
real FastAPI routes, HMAC verification, strict command models, adapter services
and temporary SQLite state. The HTTPS transport boundary and telephony repository
are isolated test fixtures. It tests lost-response recovery after restart,
historical versions, denied/missing/conflicting lookups, unchanged mutation
counts, concurrent version rejection, redaction and connection cleanup.

These source tests do not certify live mTLS, production MariaDB, downstream
Odoo/n8n delivery, SIP/audio or restore. Exact source/CI and test evidence are
recorded in the accompanying PR and issue #11.
