# CODEX DIRECTIVE — DEDICATED VICIDIAL SERVER

## Server identity

```text
Role: Dedicated VICIdial/Asterisk server
Address: 65.21.67.207
Authorized application server: 65.109.65.169
Repository target: /root/codestra-vicidial-completion
```

## Mission

Inspect, preserve, harden, and complete the VICIdial-side integration required by the Codestra platform.

This server remains dedicated to:

- VICIdial
- Asterisk
- VICIdial MariaDB
- SIP trunks and DIDs
- Campaigns
- Lists and hopper
- Users and phones
- User groups
- Inbound and closer groups
- Scripts
- Dispositions
- Callbacks
- Recordings
- Dialplan
- Telephony reports
- Restricted local Codestra adapter

Do not install Odoo, n8n, Keycloak, or the main middleware application here.

Do not return only a plan. Inspect the actual server, preserve the functioning phone system, implement the restricted adapter and missing synchronization capabilities, run tests, create backups and evidence, and commit stable code checkpoints.

## Required reading

Read completely:

1. `AGENTS.md`
2. `CODEX_VICIDIAL_SERVER.md`
3. `docs/VICIDIAL_ADAPTER_CONTRACT.md`
4. `docs/AUTOMATIC_CAMPAIGN_PROVISIONING.md`
5. `specs/VICIDIAL_ODOO_MAPPING_SPEC.md`

## Critical boundary

The application server `65.109.65.169` must not receive direct remote database access.

The restricted adapter runs locally on this VICIdial server and performs only allowlisted, validated, idempotent operations.

## First execution

Begin read-only:

1. Inventory OS, VICIdial, Asterisk, MariaDB, services, firewall, listening ports, Git repositories, backups, dialplan, campaigns, lists, users, groups, scripts, dispositions, DIDs, IVRs, callbacks, recordings, and adapters.
2. Run existing passive health checks.
3. Identify existing Codestra telephony event gateway or adapter code.
4. Preserve currently working call paths.
5. Record current Git SHA and uncommitted changes.
6. Back up configuration before mutations.
7. Create `reports/vicidial/BASELINE.md`.
8. Create a gap matrix.
9. Begin implementation after discovery.

## Restricted adapter

Implement or complete the contract in `docs/VICIDIAL_ADAPTER_CONTRACT.md`.

Requirements:

- Accept requests only from `65.109.65.169` or an approved private channel.
- Require service authentication and authorization.
- Prefer mTLS or the existing accepted secure channel.
- Do not expose a general SQL interface.
- Do not expose shell execution.
- Use strict request schemas.
- Use idempotency keys.
- Use parameterized local database operations.
- Allowlist tables, columns, and operations.
- Back up affected state.
- Perform read-back.
- Return normalized actual state.
- Record audit and evidence.
- Redact secrets.
- Provide immediate kill switch.
- Keep newly created resources disabled.

## Automatic campaign provisioning

The application server will send an approved campaign manifest.

For each manifest, the adapter must safely create or adopt:

- VICIdial campaign
- Dedicated lead lists
- User groups
- Inbound groups
- Closer groups
- Script records and versions
- Disposition statuses
- Callback settings
- Transfer policy
- Recording policy reference
- Reporting mapping
- Optional DID/IVR mapping only when explicitly approved

Rules:

- Existing compatible resources are adopted.
- Existing incompatible resources block the run.
- Retry creates no duplicates.
- Resource ownership is recorded using canonical codes or metadata supported by the current schema.
- No production activation happens during provisioning.
- Read-back must match the desired manifest.
- The adapter returns a per-resource result.
- Archive disables resources but preserves history.

## Event delivery

Implement or verify passive delivery for:

- Dial begin/end
- Answer
- Bridge enter/leave
- Hangup
- Queue join/leave
- Agent login/logout
- Pause/resume
- Disposition
- Transfer
- Callback
- Appointment preparation
- Recording reference

Requirements:

- Stable call correlation
- Asterisk boot identity where necessary
- Local-channel collapse
- Duplicate suppression
- Retry limits
- Dead letters or durable local spool
- Graceful shutdown
- Rate limits
- Circuit breaker
- Evidence
- No false event gaps during service restart

## Local database safety

Before writes:

- Identify schema and VICIdial version.
- Identify existing customizations.
- Verify table engines and constraints.
- Backup affected tables/configuration.
- Use transactions where safe.
- Use compensating operations where transactions do not span all affected state.
- Never perform destructive schema changes without explicit migration evidence.
- Never delete historical calls, recordings references, leads, or users.
- Deactivate instead of deleting users.
- Preserve native reports.

## Firewall

- Preserve SSH before changes.
- Prepare automatic rollback.
- MariaDB must not be public.
- Adapter port is restricted to `65.109.65.169` or private network.
- Administrative VICIdial interfaces retain their current approved access policy.
- Do not break SIP signaling, RTP, WebRTC, carrier, or agent traffic.
- Record before/after rules and connectivity tests.

## Campaign validation

For each campaign verify:

- Canonical campaign ID
- Active state
- Dial method
- Calling hours
- Time zone
- Default list
- Lists
- User group
- Inbound group
- Closer group
- Script
- Dispositions
- Callback settings
- Recording settings
- Transfer policy
- Hopper/pacing settings when applicable
- Read-back result

## Tests

Use synthetic leads, test users, test endpoints, approved test DIDs, and approved non-customer destinations.

Required:

- Adapter auth rejection
- Source restriction
- Schema rejection
- Idempotent campaign provision
- Compatible-resource adoption
- Incompatible-resource collision block
- Disabled-by-default
- Campaign read-back
- List allocation mapping
- User-group mapping
- Inbound/closer mapping
- Script/disposition mapping
- Agent sync
- Agent disable
- Lead publication
- Duplicate lead retry
- Callback creation/read-back
- Same-campaign transfer
- Unauthorized transfer rejection
- Full event lifecycle
- Service restart without false gaps
- Reconciliation
- Rollback

## Production safety

Do not enable production dialing merely because resources were provisioned.

Production activation requires a separate authenticated command and evidence references from the application server.

The local kill switch must immediately block:

- Campaign activation
- Lead publication
- Agent sync writes
- Call control
- Transfers
- Callback writes
- IVR changes

## Required reports

```text
reports/vicidial/BASELINE.md
reports/vicidial/GAP_MATRIX.csv
reports/vicidial/CAMPAIGN_INVENTORY.csv
reports/vicidial/GROUP_INVENTORY.csv
reports/vicidial/LIST_INVENTORY.csv
reports/vicidial/SCRIPT_INVENTORY.csv
reports/vicidial/DISPOSITION_INVENTORY.csv
reports/vicidial/ADAPTER_IMPLEMENTATION.md
reports/vicidial/ADAPTER_TESTS.md
reports/vicidial/EVENT_GATEWAY_TESTS.md
reports/vicidial/FIREWALL.md
reports/vicidial/BACKUP_RESTORE.md
reports/vicidial/RECONCILIATION.md
reports/vicidial/PRODUCTION_GATE.md
```

## Start now

Read all required files, perform read-only inventory, run current tests, preserve working telephony, create the baseline, and begin implementing the restricted adapter and missing VICIdial synchronization. Do not stop after producing a plan.
