# VICIdial/Asterisk — Codestra Telephony Fabric v1

## Authority

VICIdial/Asterisk owns telephony execution, campaigns, agents, calls, channels, dispositions, recordings and provider-facing call state. Odoo owns CRM/business history. Middleware owns authorization, command state, idempotency, capability gates, audit and reconciliation.

The only approved mutation path is:

```text
Middleware 10.40.0.1 -> restricted adapter 10.40.0.2:8443 -> local backend 127.0.0.1:8097
```

The adapter is private, mTLS-authenticated, allowlisted, and not a public general administration API. n8n has no VICIdial API, AMI, database, carrier or adapter credential; it calls Middleware only.

## Telephony API

The private Middleware facade supports:

- no-effect call preview and policy checks;
- approved call/callback requests;
- call and callback status;
- call disposition and post-call metadata;
- agent and campaign status projections;
- recording metadata references;
- signed call, agent, campaign and recording events.

No generic SQL, VICIdial admin, AMI command, shell, dialplan edit, trunk configuration, carrier management or unrestricted campaign mutation endpoint is permitted.

## Correctness

- Every command is tenant/campaign scoped and idempotent.
- Exact replay returns the existing command.
- Same key/different payload conflicts.
- Submission timeout becomes `UNKNOWN`; adapter read-back occurs before retry.
- Affected-row and expected-version checks prevent stale campaign/agent changes.
- Call placement, callback dispatch, recording and lead publication are separately capability-gated.
- Middleware cannot report success until the restricted adapter reads authoritative telephony state.

## Capabilities

```text
PRODUCTION_DIALING=false
CALLBACK_DISPATCH=false
TELEPHONY_ADMIN_WRITE=false
RECORDING_WRITE=false
LEAD_PUBLISH=false
ODOO_WRITE=false
```

## Branch program

```text
fix/vicidial-r3-dedicated-consolidation-20260827
  -> integration/codestra-telephony-fabric-v1
       -> integration/middleware-command-api-v1
       -> integration/middleware-event-outbox-v1
       -> feature/telephony-readback-v1
       -> feature/callback-policy-v1
       -> test/telephony-fabric-contracts-v1
```

No branch places a call, changes a campaign, activates callbacks, changes firewall/routing or deploys the live adapter.