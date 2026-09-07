# Repository Profile — `Vicidialer-Codestra`

## Identity

- **Repository:** `appolon1908-hue/Vicidialer-Codestra`
- **Category:** Communications runtime — voice/contact center
- **Visibility:** `private`
- **Default branch:** `main`
- **Authority:** Primary VICIdial/Asterisk voice and contact-center connector authority
- **Status:** Active private repository for voice runtime configuration, campaign controls, and governed integration.

## Purpose

Manages campaigns, agents, queues, inbound groups, lead lists, scripts, dispositions, callbacks, recordings, transfers, dialer provisioning, and voice events.

## Owns

- VICIdial/Asterisk runtime and connector configuration
- Voice campaign, agent, queue, callback, disposition, recording, and transfer state
- Call events, read-back, campaign provisioning, and contact-center operational controls

## Does not own

- Cross-campaign access or shared agent visibility
- SMS or email provider runtime
- Cross-system privileged writes that bypass Middleware

## Key integrations

- Middleware
- Odoo
- n8n
- DID/SIP providers and the communications SDK/control plane

## Current priorities

1. Enforce one-campaign-per-agent isolation and one primary supervisor per campaign
2. Complete Odoo/VICIdial mapping modules and governed connector APIs
3. Prove call command idempotency, provider read-back, callbacks, recordings, and reconciliation
4. Keep production dialing disabled until campaign, trunk, compliance, and launch evidence passes

## Governance and safety

- Promotion model: `feature/docs/fix/security/upgrade -> development -> test -> staging -> production -> main`.
- Use pull requests and exact-head/merge-result validation; source merge never authorizes dialing.
- Never commit SIP credentials, phone lists, recordings, customer PII, database dumps, or provider secrets.
- Campaign isolation and live-call gates are permanent production requirements.
- This document does not place calls, alter live campaigns, provision agents, change trunks, or deploy VICIdial.

## Account-wide catalog

See `appolon1908-hue/documentaions/REPOSITORY_CATALOG.md`.
