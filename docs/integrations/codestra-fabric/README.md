# VICIdial/Asterisk — Codestra Telephony Fabric v1

## Authority

VICIdial/Asterisk owns telephony execution, campaigns, agents, calls, channels, dispositions, recordings, callbacks, transfers, and provider-facing call state. Odoo owns approved CRM/business state. Middleware owns caller authorization, tenant and campaign resolution, durable command state, idempotency, capability gates, audit, event acceptance, and reconciliation.

The only approved mutation path is:

```text
Middleware 10.40.0.1
    -> restricted adapter 10.40.0.2:8443
    -> local backend 127.0.0.1:8097
    -> VICIdial / Asterisk
    -> authoritative read-back
```

The adapter is private, mTLS-authenticated, allowlisted, schema-validated, and not a public general-administration API. n8n has no VICIdial API, AMI, ARI, database, carrier, or adapter credential; it calls Middleware only.

## Permanent campaign isolation

The source contract is fail-closed:

- each agent belongs to exactly one campaign;
- each campaign has exactly one primary supervisor;
- an agent cannot log in to, receive leads or calls from, transfer into, schedule callbacks for, view recordings from, report on, or receive campaign email for another campaign;
- scripts, dispositions, inbound groups, lead lists, callbacks, recordings, transfer routes, dashboards, and workflows are campaign-scoped;
- every command carries authoritative tenant and campaign context resolved by Middleware;
- a provider callback can never choose or replace tenant or campaign authority.

## Provider-neutral commands

```text
voice.call.preview.v1
voice.call.place.v1
voice.call.cancel.v1
voice.callback.schedule.v1
voice.callback.cancel.v1
voice.disposition.record.v1
```

## Canonical events

```text
voice.call.requested.v1
voice.call.queued.v1
voice.call.ringing.v1
voice.call.answered.v1
voice.call.completed.v1
voice.call.failed.v1
voice.call.indeterminate.v1
voice.callback.scheduled.v1
voice.callback.completed.v1
voice.disposition.recorded.v1
voice.recording.available.v1
```

## Unknown-outcome safety

A call-placement or callback timeout is never interpreted as permission to try again.

```text
telephony execution attempts after uncertainty = 1
blind redial = 0
automatic command resubmission = 0
authoritative restricted-adapter read-back = required
intermediate state = reconciliation_required
unresolved state = manual_review
```

Read-back may be retried in a bounded workflow; call placement may not. Middleware cannot report success until the restricted adapter verifies authoritative VICIdial/Asterisk state.

## Private API

The private Middleware facade covers:

- no-effect call preview;
- call request, read, cancellation, and event timeline;
- callback request, read, cancellation, and completion;
- call disposition;
- campaign-scoped agent and campaign status;
- authorized recording metadata references;
- provider health and aggregate usage;
- signed, replay-protected VICIdial event ingress.

Every effectful request requires exact idempotency. Every tenant/campaign resource requires both tenant and campaign context. The event ingress requires mTLS and signed event headers.

No generic SQL, VICIdial admin, AMI command, ARI command, shell, dialplan edit, trunk configuration, carrier management, or unrestricted campaign mutation endpoint is permitted.

## Source validation

```bash
python vicidial/scripts/validate_codestra_telephony_fabric.py
python -m pytest -q vicidial/tests/test_codestra_telephony_fabric.py
```

The validator checks the machine-readable authority, campaign isolation, command/event registry, private OpenAPI paths, OAuth scopes, mTLS webhook boundary, idempotency requirements, zero-redial rule, and disabled live-effect state.

## Capabilities

```text
PRODUCTION_DIALING=false
CALLBACK_DISPATCH=false
TELEPHONY_ADMIN_WRITE=false
RECORDING_WRITE=false
LEAD_PUBLICATION=false
ODOO_WRITE=false
CALLS_PLACED=0
DEPLOYMENT_AUTHORIZED=false
PRODUCTION_AUTHORIZED=false
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

This branch remains stacked on the reconciled R3 foundation until that source is independently reviewed and accepted. No child branch places a call, changes a campaign, activates callbacks, changes firewall/routing, or deploys the live adapter.
