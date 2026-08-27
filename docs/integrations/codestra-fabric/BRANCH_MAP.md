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

The existing immutable-image, source-authority and runtime-hardening work remains separate. No child branch changes live dialing or public exposure.