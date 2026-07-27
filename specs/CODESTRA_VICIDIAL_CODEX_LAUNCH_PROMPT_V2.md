# Codestra VICIdial Cross-System Mapping — Codex Launch Prompt v2.0

Canonical specification path:

`/root/codestra-production-completion/specs/CODESTRA_VICIDIAL_CROSS_SYSTEM_MAPPING_MASTER_SPEC_V2.md`

Expected SHA-256:

`a13ac29307234ceab28bc5d30ad086c09f1fb58c8cb067a3d5dd821b31909374`

Canonical mapping-template path:

`/root/codestra-production-completion/specs/CODESTRA_VICIDIAL_CAMPAIGN_MAPPING_TEMPLATE_V2.csv`

Expected SHA-256:

`db9f204ff4c6617e3bdf2fc35c1ea2a94dd4828d65ee26efc554c161d58c0f8b`

```text
Authenticate and execute the complete VICIdial cross-system mapping
specification at:

/root/codestra-production-completion/specs/CODESTRA_VICIDIAL_CROSS_SYSTEM_MAPPING_MASTER_SPEC_V2.md

Expected SHA-256:

a13ac29307234ceab28bc5d30ad086c09f1fb58c8cb067a3d5dd821b31909374

Also authenticate and use the mapping template at:

/root/codestra-production-completion/specs/CODESTRA_VICIDIAL_CAMPAIGN_MAPPING_TEMPLATE_V2.csv

Expected SHA-256:

db9f204ff4c6617e3bdf2fc35c1ea2a94dd4828d65ee26efc554c161d58c0f8b

Begin with the mandatory read-only Server A and Server B discovery.

Do not start by creating campaigns.

First inspect actual VICIdial MariaDB field widths, identifier rules, existing
campaigns, lists, user groups, inbound groups, scripts, statuses, DIDs,
Asterisk contexts, Odoo campaigns, middleware mappings, n8n references,
feature flags, backups, locks, and evidence.

Produce an identifier-constraint and collision report.

Do not assume canonical codes such as COD-WEB-OUT fit every physical VICIdial
field. Preserve the canonical Odoo/middleware code and generate a deterministic,
unique physical alias only when required by the actual schema.

Reuse compatible records. Do not create duplicates.

Use Server A as the application/control-plane orchestrator and the approved
restricted Server A-to-Server B connection for telephony work.

Do not require reverse SSH from Server B.

Route every operational telephony write through authenticated middleware and
the approved Server B adapter.

Never permit direct operational writes from Odoo, n8n, browser clients, or AI
services into VICIdial MariaDB.

Keep all production feature flags false.

Keep all production campaigns, lead publication, IVR, callbacks, agent sync,
provisioning, live call control, and dialing disabled.

Use synthetic leads, test users, isolated endpoints, test campaigns, test DIDs,
and approved non-customer destinations only.

Create and verify rollback checkpoints before every mutation.

Apply one bounded change set at a time, read back authoritative state, and
reconcile before continuing.

Keep live transfers inside the active campaign and prove cross-campaign live
transfer rejection.

Never expose secrets, raw SIP authorization, complete protected telephone
numbers, customer recordings, or unrestricted customer data in source,
commands, logs, traces, fixtures, reports, or evidence.

Correct defects autonomously and rerun every affected test.

A PASS requires configuration, middleware mapping, functional execution,
authoritative read-back, reconciliation, and checksummed evidence.

Return only for a true external blocker, mandatory rollback, or completion of
the authorized staging implementation.

Do not enable production.

Maximum final recommendation: STAGING-ONLY.
```
