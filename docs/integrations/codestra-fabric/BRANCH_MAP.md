# Telephony fabric branch map

```text
fix/vicidial-r3-dedicated-consolidation-20260827
  -> integration/codestra-telephony-fabric-v1
       -> integration/middleware-command-api-v1
       -> integration/middleware-event-outbox-v1
       -> feature/telephony-readback-v1
       -> feature/callback-policy-v1
       -> test/telephony-fabric-contracts-v1
```

## Current source lock

```text
FOUNDATION_BRANCH=fix/vicidial-r3-dedicated-consolidation-20260827
FOUNDATION_SHA=7992499fa977b12c3ba7bfe7bbfc697379d867c6
FOUNDATION_PR=2
TELEPHONY_BRANCH=integration/codestra-telephony-fabric-v1
```

The telephony fabric remains stacked on the corrected foundation until PR #2 receives independent exact-head review and is accepted. After that merge, PR #3 must be retargeted to the protected merged `main` SHA and revalidated before any child implementation branch is promoted.

The immutable-image, source-authority, runtime-hardening, durable event-acknowledgement, operational readiness, and complete-backlog safety history remains preserved. No child branch changes live dialing, callbacks, public exposure, carrier routing, firewall state, or production deployment.
