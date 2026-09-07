# Codex Mission — Complete Codestra VICIdial to Production Readiness

## Mission

Complete the dedicated `appolon1908-hue/Vicidialer-Codestra` project. Do not stop at audit or documentation. Implement, test, reconcile, push, review and certify every milestone in `docs/PRODUCTION_COMPLETION_MILESTONES.md` that can be completed with available repository and runtime access.

The target is production readiness with customer traffic still disabled. Activation is a separate explicit decision.

## Current source authority

At mission creation, production-completion work is stacked on the exact green consolidation lineage rather than stale `main`:

```text
base branch: fix/vicidial-r3-dedicated-consolidation-20260827
base exact head: 79eb9bc3b3e91102c88b214b05476d0782496bc9
production-completion branch: codex/vicidial-production-completion
```

Always fetch GitHub first and use newer forward commits when the branch has advanced. Never reset or force-push away reviewed work.

## Complete authority

Codex may, without asking for routine approval:

- modify any source, test, docs, config, Docker, systemd, Apache/private-ingress, firewall-template, monitoring, release or CI file in this dedicated repository;
- create focused branches and commits;
- push continuously;
- open and update pull requests;
- address review feedback;
- run offline/unit/integration/container/security tests;
- inspect the VICIdial host when authorized runtime access is available;
- take pre-change backups and checksums;
- perform safe staging/shadow deployment and service reload/restart when required for a non-customer synthetic acceptance test;
- reconcile source/runtime drift;
- repair least-privilege database grants;
- validate MyISAM backup and isolated restore;
- deploy private mTLS/firewall changes after preserving SSH/SIP/RTP/telephony and recording rollback;
- create and remove synthetic disabled test resources;
- merge only when the repository's exact-head gates, required independent approval, and branch/ruleset policy actually permit it.

Codex must stop for explicit authorization before enabling real customer PSTN dialing or another irreversible external/customer action.

## Immutable ownership boundary

```text
Odoo / agent UI
  -> Codestra Middleware
  -> signed, authorized, idempotent command
  -> private mTLS
  -> Restricted VICIdial Adapter
  -> local VICIdial / Asterisk
  -> actual-state readback
  -> Middleware
  -> Odoo / event consumers
```

Never introduce:

- remote MariaDB access;
- Odoo or n8n direct VICIdial SQL;
- browser direct VICIdial administration;
- generic SQL, shell or arbitrary filesystem APIs;
- generic AMI/ARI passthrough;
- customer credentials or recordings in Git/evidence;
- feature-branch images as production authority.

## Code-completeness rule

A feature is not complete when the production path contains or depends on:

```text
pass-only implementation
NotImplementedError
TODO/FIXME standing in for implementation
placeholder endpoint
hard-coded success
mock persistence
fake runtime state
candidate/reference-only runtime source
silent failure
write without actual-state readback
```

Mocks and synthetic data belong only in tests/fixtures. Production code must use real persistence or a real controlled adapter and have error handling, authorization, idempotency where applicable, audit and readback.

## Canonical feature switches

Use only these canonical activation names:

```text
ENABLE_VICIDIAL_WRITES
ENABLE_PROVISIONING
ENABLE_AGENT_SYNC
ENABLE_LEAD_PUBLICATION
ENABLE_LIVE_CALL_CONTROL
ENABLE_WARM_TRANSFER
ENABLE_IVR_ROUTING
ENABLE_IVR_CALLBACKS
ENABLE_PRODUCTION_DIALING
ENABLE_SMS_DELIVERY
ENABLE_EMAIL_DELIVERY
ENABLE_EXTERNAL_DELIVERY
ENABLE_AI_ACTIONS
```

Deprecated aliases such as `ENABLE_CALLBACK_WRITES`, `ENABLE_TRANSFER_POLICY_SYNC`, and `ENABLE_PSTN_DIALING` are not valid activation controls and must fail closed in production authority.

Normal staging/production-candidate state keeps all canonical switches false. The only bounded exception is the existing request-bound `production-reconciliation` profile: `ENABLE_VICIDIAL_WRITES=true` and `ENABLE_PROVISIONING=true` may be used only when the immutable reconciliation policy, exact release SHA, owner authorization and fresh backup are all verified; every live/customer capability remains false.

## Runtime safety

Before changing the VICIdial/Asterisk host:

1. record hostname, time, exact release/source identity and git/runtime digest;
2. capture listeners, firewall, routes and service health;
3. inventory Asterisk/VICIdial versions and current campaigns/agents without exposing PII;
4. snapshot affected config/table state and checksum it;
5. preserve SSH, SIP, RTP and carrier connectivity;
6. prove rollback commands before applying the change.

Do not infer that repository tests prove the live host. Runtime certification is separate evidence.

## Required execution order

Follow M0 through M15 in `docs/PRODUCTION_COMPLETION_MILESTONES.md`. Reuse existing valid implementation instead of rebuilding it. Never skip a milestone because documentation claims it passed in an older environment.

After every milestone, push a checkpoint containing:

```text
MILESTONE=
BRANCH=
HEAD_SHA=
FILES_CHANGED=
TESTS=
CI_RUNS=
RUNTIME_ENVIRONMENT=
RUNTIME_EVIDENCE=
ROLLBACK=
P0_OPEN=
P1_OPEN=
BLOCKERS=
NEXT_MILESTONE=
```

## Release and merge discipline

- `main` is the eventual sole long-lived canonical source authority.
- No force-push/rebase of reviewed production branches.
- Require exact-head source/container/completeness CI.
- Require secret scan, SBOM, SLSA/provenance and vulnerability gates.
- Resolve review threads.
- Require an independent approval when repository governance requires one.
- Build the final production image only from the exact protected merged `main` SHA.
- Sign and verify the exact immutable registry digest.
- Deploy by digest, never mutable tag.

## Final target

Do not print `PRODUCTION_READY=YES` until all code and runtime gates in the milestone authority are actually verified. The target state is:

```text
P0_OPEN=0
P1_OPEN=0
PLACEHOLDER_RUNTIME_CODE=0
CONFIG_CONTRACT=PASS
ADAPTER_API=PASS
HMAC_AUTH=PASS
MTLS=PASS
SOURCE_FIREWALL=PASS
MARIADB_LOCAL_ONLY=PASS
DB_LEAST_PRIVILEGE=PASS
MYISAM_RESTORE=PASS
CAMPAIGN_PROVISIONING=PASS
AGENT_SYNC=PASS
LEAD_PUBLICATION=PASS
CALLBACKS=PASS
DISPOSITIONS=PASS
TRANSFER_POLICY=PASS
AGENT_STATE=PASS
CLICK_TO_CALL=PASS
CALL_CONTROL=PASS
SCREEN_POP=PASS
CALL_EVENTS=PASS
RESTART_NO_GAP=PASS
MIDDLEWARE_CONTRACT=PASS
ODOO_CONTRACT=PASS
N8N_BOUNDARY=PASS
RECONCILIATION=PASS
OBSERVABILITY=PASS
BACKUP_RESTORE=PASS
ROLLBACK=PASS
CI=GREEN
SBOM=PASS
SLSA=PASS
TRIVY=PASS
GRYPE=PASS
SECRET_SCAN=PASS
MAIN_PROTECTED=PASS
RELEASE_SHA=PINNED
IMAGE_DIGEST=PINNED
IMAGE_SIGNATURE=PASS
RUNTIME_SOURCE_MATCH=PASS
PRODUCTION_READY=YES
PRODUCTION_DIALING=DISABLED
CUSTOMER_TRAFFIC=NOT_AUTHORIZED
```

If a gate cannot be completed because access, provider service, independent approval, GitHub runner availability, or another external dependency is unavailable, record the exact blocker and continue every independent milestone that remains possible. Do not convert `NOT_RUN` or `BLOCKED` into `PASS`.
