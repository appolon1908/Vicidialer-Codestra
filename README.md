# Vicidialer Codestra

Dedicated Codestra VICIdial/Asterisk integration and telephony authority.

This repository is the **VICIdial/Asterisk voice and contact-center connector authority** for Codestra. It owns the restricted VICIdial adapter, campaign and agent integration, lead/callback/transfer workflows, call-event reconciliation, deployment and operations definitions, immutable container packaging, CI/security checks, tests, and release evidence.

Telnexa/Jasmin is the separate **SMS** authority and Klyrow/Postal is the separate **email** authority. Neither owns VICIdial dialing, Asterisk queues, call campaigns, recordings, dispositions, callbacks, or transfers.

The permanent cross-system ownership path is:

```text
VICIdial / Asterisk
        ^  |
        |  v
Vicidialer-Codestra
        ^  |
        |  v
Codestra Middleware
   /      |        \
Odoo     n8n    Telnexa / Klyrow
business orchestration   SMS / Email
state
```

- Middleware is the only remote cross-system command/write authority.
- n8n orchestrates through Middleware only and never calls VICIdial, Asterisk, AMI, ARI, MariaDB, or a carrier directly.
- Odoo owns approved CRM and business desired state; this connector does not write directly to the Odoo database.
- Kong is the API-policy boundary and Keycloak supplies service identity.
- The restricted adapter remains private, authenticated, schema-validated, idempotent, audited, and read-back verified.
- New campaigns, lists, scripts, users, callbacks, and routes remain disabled or non-dialing by default.
- Production dialing and live call control are separate activation gates.

The repository contains the restricted VICIdial adapter, campaign-provisioning design,
canonical event/synchronization code, private-ingress and systemd definitions, container
packaging, CI/security checks, tests, release manifests, operational runbooks and retained
migration/release history.

## Production completion authority

Read these before implementation or deployment:

- `AGENTS.md`
- `docs/PRODUCTION_COMPLETION_MILESTONES.md`
- `specs/CODEX_VICIDIAL_PRODUCTION_COMPLETION_MISSION.md`
- `vicidial/docs/VICIDIAL_ADAPTER_CONTRACT.md`
- `vicidial/docs/AUTOMATIC_CAMPAIGN_PROVISIONING.md`
- `RUNTIME_TO_SOURCE_RECONCILIATION.md`

The authoritative integration boundary is:

```text
Odoo / agent UI
  -> Codestra Middleware
  -> signed + idempotent private mTLS command
  -> restricted adapter
  -> local VICIdial / Asterisk
  -> actual-state readback
  -> Middleware / Odoo / event consumers
```

Odoo, n8n and browsers never write VICIdial directly. MariaDB remains local-only. No
generic SQL, shell, filesystem, AMI or ARI proxy belongs in this service.

## Safety

Production readiness and production traffic activation are separate gates. Newly
provisioned resources remain disabled and all customer/live capabilities remain
fail-closed during build, CI, shadow deployment and certification. The final production
artifact must be built, signed, verified and deployed by immutable digest from the exact
protected merged `main` SHA after required review and runtime evidence pass.
