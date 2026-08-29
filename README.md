# Vicidialer Codestra

## Repository authority

This repository is the **VICIdial/Asterisk voice and contact-center connector authority** for Codestra. It owns the restricted VICIdial adapter, campaign/agent/lead/callback/transfer integration, voice-system reconciliation, deployment/operations definitions, container packaging, CI/security checks, tests and release evidence.

Telnexa/Jasmin is a separate **SMS** platform. It does not own VICIdial voice dialing, call campaigns, Asterisk queues, recordings, dispositions or transfers.

The permanent cross-system path is:

```text
VICIdial / Asterisk
        ^  |
        |  v
Vicidialer-Codestra
        ^  |
        |  v
    Middleware
   /    |      \
Odoo   n8n   Telnexa/Klyrow
              SMS / Email
```

- Middleware remains the only cross-system command/write authority.
- n8n orchestrates through Middleware only and must not call VICIdial directly.
- Odoo receives/owns approved CRM/business state; the connector must not write directly to Odoo's database.
- Telnexa/Jasmin is used for SMS only.
- Klyrow/Postal is used for email/customer communications only.
- Kong is the API/security boundary and Keycloak supplies service identity.

The restricted VICIdial adapter must remain private/authenticated, enforce schema validation and idempotency, and perform destination read-back before a mutation is trusted. New campaign/list/script resources remain disabled or non-dialing by default. Production dialing and live call control remain separate activation gates.

## Dedicated repository history

Dedicated, history-preserving extraction of the Codestra VICIdial integration work.

The repository contains the restricted VICIdial adapter, campaign-provisioning gateway,
deployment and operations definitions, container packaging, CI/security checks, tests,
release manifests, and the retained VICIdial development/release branches.

The `main` branch is initialized from `fix/vicidial-r3-phase3-remediation`.
Historical workflow revisions are retained under `ci/workflow-history/`; the current
active GitHub Actions workflows are installed after the history import completes.

Production deployment remains governed separately by protected review, immutable image
digest, signature verification, host/database preflight, and rollback controls.
