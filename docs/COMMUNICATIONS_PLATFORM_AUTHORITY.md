# Communications Platform Authority — VICIdial / Voice

## Purpose

This document defines `appolon1908-hue/Vicidialer-Codestra` as the principal Codestra voice/contact-center integration authority inside the unified communications platform.

## Permanent ownership

This repository owns:

- VICIdial/Asterisk restricted connector and adapter runtime;
- voice campaign, agent, queue, lead, callback, disposition, recording-metadata and transfer integration;
- telephony read-back and reconciliation;
- extension/session provisioning contracts owned by the voice connector;
- voice-specific deployment, health, security, tests, operations and release evidence.

This repository does not own:

- cross-system authorization or durable command state — `Middleware-`;
- public API gateway policy — `Kong`;
- identity issuance — `Keycloak`;
- SMS — `telnexa`;
- email — `klyrow.com`;
- CRM/business state — `Odoo`;
- orchestration — `N8N`;
- public SDK contracts — `SDK-repository`;
- shared edge TLS/routing — `Caddy`.

## Required path

```text
Application / SDK
      -> Caddy
      -> Kong
      -> Keycloak-validated identity
      -> Middleware
      -> VICIdial trusted adapter
      -> Vicidialer-Codestra restricted gateway
      -> VICIdial / Asterisk
```

No browser, n8n workflow, Odoo module or product application may invoke privileged VICIdial mutations directly.

## Voice command surface

Canonical communications contracts should model voice operations using provider-neutral command names while mapping inside Middleware/connector code to VICIdial-specific operations.

Required capability families include:

- campaign health and availability;
- agent/session provisioning and revocation;
- lead/callback submission;
- call initiation where separately enabled;
- transfer requests;
- disposition updates;
- queue/campaign membership;
- recording metadata lookup;
- command status and authoritative read-back;
- reconciliation of unknown/indeterminate outcomes.

Every effectful operation must require tenant, actor/service identity, correlation ID, idempotency key and explicit capability authorization.

## Event surface

Voice events emitted toward Middleware must be normalized into canonical event contracts. At minimum the platform should support:

- call started;
- call connected;
- call ended;
- disposition updated;
- callback created/updated;
- transfer requested/completed/failed;
- recording metadata available;
- agent/session status changed;
- provider command failed;
- reconciliation completed.

Provider-local event payloads are not public SDK contracts until normalized and versioned.

## Campaign isolation

Campaign isolation is mandatory. An agent belongs to exactly one active campaign context unless an explicitly reviewed administrative role says otherwise. Agents must not receive, view, transfer into, report on, or act on another campaign's leads, callbacks, recordings, scripts or queues. Campaign supervisor permissions are campaign-scoped. Global administrative access must be separately authorized and audited.

## Safety rules

1. Production dialing remains disabled unless separately approved.
2. A code merge never activates live call placement.
3. Unknown provider outcomes remain indeterminate and require read-back/reconciliation before retry.
4. No direct database writes from Middleware, Odoo, n8n or SDK clients into VICIdial tables.
5. Restricted connector ingress stays private/authenticated.
6. Credentials, SIP secrets, recordings, PII and live tenant mappings never enter Git.
7. Call-control operations require explicit capability and rate/concurrency controls.
8. Every command and event must be tenant-scoped and auditable.
9. Recording access must use least privilege and retention policy.
10. Emergency kill switches must be able to stop new dialing without preventing read-only status/reconciliation.

## Cross-repository contract requirements

Changes affecting voice communications require coordinated evidence from:

- `SDK-repository` — public/generated contract compatibility;
- `Kong` — route/scope/audience policy;
- `Keycloak` — client/scope/audience contract when identity changes;
- `Middleware-` — command, ledger, adapter and reconciliation behavior;
- `Vicidialer-Codestra` — provider behavior and read-back;
- `Odoo` — CRM mapping when business state changes;
- `N8N` — workflow compatibility where orchestration consumes events;
- `Caddy` — edge/private-ingress compatibility if routing changes.

## Release gates

Before any live voice capability is enabled:

1. exact-head CI is green in every affected repository;
2. API/event contracts are semantically valid and drift checks pass;
3. invalid identity/scope/tenant requests fail closed;
4. duplicate commands are proven idempotent;
5. timeout/network-reset cases are proven indeterminate rather than retried blindly;
6. destination read-back and reconciliation are proven;
7. staging campaign isolation is verified;
8. no live dialing occurs during ordinary CI/staging tests;
9. rollback and emergency disable are rehearsed;
10. explicit production activation approval is recorded separately from merge approval.

## Branching

Use short-lived `feature/*`, `fix/*`, `docs/*`, and `test/*` branches. Promote reviewed work through the repository's accepted integration/release flow. Documentation changes do not authorize runtime activation.
