# Codestra VICIdial Production Completion Authority

This document is the execution authority for completing the dedicated Codestra VICIdial integration. It is intentionally implementation-first: a milestone is complete only when code, tests, CI, runtime evidence, and rollback evidence agree.

## Immutable ownership boundary

```text
Odoo / agent desktop
  -> Codestra Middleware
  -> signed + idempotent private command
  -> mTLS 10.40.0.1 -> 10.40.0.2:8443
  -> restricted adapter 127.0.0.1:8097
  -> local VICIdial / Asterisk
  -> read-back
  -> middleware
  -> Odoo / event consumers
```

Odoo and n8n never write directly to VICIdial. MariaDB remains local-only. The restricted adapter never exposes generic SQL, shell, filesystem, AMI, or ARI proxy endpoints.

## Global safety state

Until separately approved production activation, all live/external capabilities remain false:

```text
ENABLE_VICIDIAL_WRITES=false
ENABLE_PROVISIONING=false
ENABLE_AGENT_SYNC=false
ENABLE_LEAD_PUBLICATION=false
ENABLE_LIVE_CALL_CONTROL=false
ENABLE_WARM_TRANSFER=false
ENABLE_IVR_ROUTING=false
ENABLE_IVR_CALLBACKS=false
ENABLE_PRODUCTION_DIALING=false
ENABLE_SMS_DELIVERY=false
ENABLE_EMAIL_DELIVERY=false
ENABLE_EXTERNAL_DELIVERY=false
ENABLE_AI_ACTIONS=false
```

## Completion milestones

### M0 — Repository authority

- Start from the exact reviewed consolidation lineage, not stale `main`.
- Reconcile PR #2 and later forward commits.
- Keep `main` as the eventual sole long-lived source authority.
- Classify historical branches without destructive cleanup until reachability and release-evidence checks pass.

Acceptance: exact head identified, source/container CI green, no unresolved review thread, no hidden alternate production source.

### M1 — Runtime source and configuration contract

- Production runtime uses canonical executable modules, not `.candidate` or `.reference` files.
- Docker, systemd, env examples, application source, and CI use one feature-flag vocabulary.
- Unknown or stale flag aliases are not treated as activation controls.
- Production and shadow Compose remain fail-closed.

Acceptance: canonical runtime modules compile; configuration regression tests pass; no deployment authority executes legacy candidate/reference paths.

### M2 — Restricted adapter certification

Certify health/version/readiness, campaigns, agents, leads, callbacks, transfer policies, reconciliation, HMAC v2 authentication, nonce/replay protection, rate limiting, idempotency, audit and read-back.

Acceptance: API/OpenAPI contract tests, negative authorization tests, retry/idempotency tests and state durability tests pass.

### M3 — Private mTLS and source firewall

Canonical path:

```text
10.40.0.1 -> 10.40.0.2:8443 -> 127.0.0.1:8097
```

Validate source firewall, client/server certificate EKU/SAN/expiry, CA separation, query/header trust, and rollback. Do not disturb separately owned 9443/9444 listeners without proof.

Acceptance: unauthorized network source denied; approved private mTLS source succeeds; adapter is not public.

### M4 — MariaDB / MyISAM safety

- MariaDB loopback/socket only.
- Least-privilege adapter and reconciliation identities.
- Parameterized allowlisted DML only.
- No destructive schema or history deletion.
- Snapshot affected MyISAM tables before controlled mutation.
- Prove isolated restore and rollback.

Acceptance: grant audit, backup checksums, isolated restore and rollback all PASS.

### M5 — Campaign provisioning

Complete the documented desired-state lifecycle from design through `provisioned_disabled`, including atomic list allocation, groups, scripts, dispositions, callback policy, transfer policy, recording-policy reference and reporting mapping.

Acceptance: retry produces no duplicate; collision/adoption tests pass; read-back exactly matches approved desired state; resources remain disabled.

### M6 — Agent / lead / callback / disposition workflows

Implement and prove agent ownership and campaign isolation, disabled-first synchronization, lead allowlists, callback persistence/timezone correctness, disposition mapping and complete read-back.

Acceptance: cross-campaign attempts fail closed; duplicate retries are idempotent; every successful mutation has actual-state evidence.

### M7 — Narrow runtime telephony control

Only if required by the middleware contract, add narrowly scoped commands for agent session state, click-to-call/originate, hangup, hold/resume and warm transfer. Never expose generic AMI/ARI passthrough.

Acceptance: authoritative agent/campaign permission is checked for every command; external dialing remains gated; synthetic safe-destination tests pass before any production use.

### M8 — Durable event lifecycle

Normalize and durably deliver agent/call/screen-pop/disposition/callback/recording events with event ID, correlation ID, call ID, agent ID, campaign ID, business unit, timestamp, sequence and schema version.

Acceptance: restart/no-gap, duplicate, retry, dead-letter and replay tests pass; event loss is observable and reconcilable.

### M9 — Middleware / Odoo / n8n contracts

Middleware is the sole remote writer. Odoo owns business desired state. n8n consumes approved asynchronous events and never writes VICIdial directly.

Acceptance: contract tests prove the write boundary, campaign isolation, normalized read-back and no direct DB credentials in downstream systems.

### M10 — Reconciliation

Schedule desired-vs-actual reconciliation and classify drift as `NONE`, `SAFE_AUTO_REPAIR`, `APPROVAL_REQUIRED`, `CRITICAL`, or `UNKNOWN`. Never auto-repair destructive drift.

Acceptance: intentional drift is detected, persisted, correlated and either safely repaired or blocked for approval.

### M11 — Observability and recovery

Metrics and alerts cover adapter availability/latency/auth failures/replay/rate limits/DB errors/readback mismatch/provisioning/agent sync/lead publication/callbacks/call commands/event backlog/reconciliation drift. Backups and runbooks are exercised.

Acceptance: alert test, backup restore, service restart and rollback evidence PASS.

### M12 — Synthetic staging

Use an isolated synthetic campaign and non-customer destinations. Exercise provisioning, retries, agent, lead, screen pop, callback, disposition, transfer, event flow, reconciliation, restart and rollback. PSTN remains blocked unless a separately approved safe test destination is supplied.

Acceptance: complete evidence packet with zero customer traffic.

### M13 — Immutable release

Build only from exact merged protected `main` SHA. Require source and container CI, Gitleaks, Trivy, Grype, SBOM, SPDX, SLSA provenance, OCI subject/digest verification and portable checksums.

Acceptance: signed immutable registry image by digest, exact source binding and verification PASS.

### M14 — Production certification

Run live-host preflight without customer dialing. Verify runtime/source identity, mTLS/firewall, DB grants, certificates, service health, Asterisk/VICIdial compatibility, monitoring, restore and rollback.

Acceptance:

```text
P0_OPEN=0
P1_OPEN=0
SOURCE_TREE=CLEAN
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
RELEASE_SHA=PINNED
IMAGE_DIGEST=PINNED
IMAGE_SIGNATURE=PASS
RUNTIME_SOURCE_MATCH=PASS
PRODUCTION_READY=YES
PRODUCTION_DIALING=DISABLED
CUSTOMER_TRAFFIC=NOT_AUTHORIZED
```

### M15 — Separate production activation

Activation is not part of production-readiness certification. It requires an explicit authorized change after M14 passes. No code review, CI run, artifact publication, or deployment may implicitly enable customer dialing.

## Implementation quality rule

Production functionality is not implemented if it relies on a no-op, fake success, hard-coded runtime response, placeholder endpoint, `NotImplementedError`, test double, candidate/reference-only source, or documentation-only behavior. Every claimed feature requires real code, real persistence or a real adapter, authorization, idempotency where applicable, audit, read-back, tests, CI and runtime evidence.
