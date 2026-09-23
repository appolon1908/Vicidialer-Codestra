# AGENTS.md — Codestra VICIdial Production Authority

Repository: `appolon1908-hue/Vicidialer-Codestra`

Primary runtime boundary:

- Codestra application/middleware side: `10.40.0.1` / `65.109.65.169`
- VICIdial/Asterisk side: `10.40.0.2` / `65.21.67.207`
- Canonical private adapter ingress: `10.40.0.2:8443`
- Restricted adapter backend: `127.0.0.1:8097`

Read these first:

- `docs/PRODUCTION_COMPLETION_MILESTONES.md`
- `docs/VICIDIAL_ADAPTER_CONTRACT.md`
- `vicidial/docs/AUTOMATIC_CAMPAIGN_PROVISIONING.md`
- `RUNTIME_TO_SOURCE_RECONCILIATION.md`
- the current consolidation PR and exact-head CI evidence

## Non-negotiable architecture

- Odoo owns business desired state.
- Codestra Middleware is the sole remote operational write authority.
- VICIdial is the telephony system of record.
- Asterisk is the call engine.
- The restricted local adapter is the only controlled VICIdial mutation authority.
- n8n is an asynchronous consumer and never writes directly to VICIdial.
- MariaDB stays local-only; never expose it to application servers or the public Internet.
- Never add generic SQL, shell, filesystem, AMI, or ARI proxy endpoints.

## Agent execution rules

- Implement and test; do not only advise.
- Preserve compatible existing work and reuse validated code.
- Do not discard unrelated changes or rewrite history.
- Never commit, print, log, or upload credentials, customer data, recordings, database dumps, private keys, or live provider secrets.
- Begin runtime work with read-only inventory and exact source/runtime identity capture.
- Back up and checksum affected state before controlled mutations.
- Every state-changing command is authenticated, authorized, schema-validated, idempotent where applicable, correlated, audited, and read back.
- A successful API response may never stand in for failed or unverified persistence.
- Existing compatible resources are adopted only with explicit ownership proof; incompatible collisions block.
- Never delete historical calls, leads, users, recordings metadata, dispositions, or reporting history.
- New campaign resources are disabled/non-dialing by default.
- Activation is a separate explicit production decision after certification.
- Every PASS must name exact commit/image identity, test command/run, environment, timestamp, and evidence.
- Backups, isolated restore proof, monitoring, reconciliation and rollback are required before production activation.

## Production code rule

No production feature is complete when runtime behavior depends on `pass`, `NotImplementedError`, placeholder/no-op logic, hard-coded success, mock persistence, fake runtime state, or `.candidate` / `.reference` files. Test doubles belong only in tests. Canonical executable modules must be the same modules compiled/tested by CI and used by deployment authority.

## Required fail-closed defaults

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

Do not turn production dialing or customer traffic on as part of ordinary development, certification, CI, staging, or repository cleanup.

<!-- CODESTRA_AGENT_PROTOCOL_V1 -->
## Codestra continuation contract

Canonical protocol:
https://github.com/ingtrader21-spec/codestra/blob/main/docs/AGENT-CONTINUATION-PROTOCOL.md

Quick start:
https://github.com/ingtrader21-spec/codestra/blob/main/docs/AGENT-QUICKSTART.md

Before changing code:
1. Read `.codestra-mission/*` when present.
2. Read the active Linear issue and linked Notion architecture.
3. Inspect exact Git branch/HEAD/dirty/worktree/upstream/PR/CI state.
4. Preserve all existing local work.
5. If acting as Builder, verify exclusive issue ownership and use a dedicated worktree.
6. Do not invent or self-assign the next task.
7. Update GitHub + Linear + Notion + the mission checkpoint before handoff.
8. Do not cross the live-production approval boundary.

The canonical protocol's no-loss, one-writer, protected-merge, checkpoint, and production-boundary rules are mandatory.
