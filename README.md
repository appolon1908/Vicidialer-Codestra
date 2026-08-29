# Vicidialer Codestra

Dedicated Codestra VICIdial/Asterisk integration and telephony authority.

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
