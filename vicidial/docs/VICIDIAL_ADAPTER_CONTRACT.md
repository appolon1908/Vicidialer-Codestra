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
