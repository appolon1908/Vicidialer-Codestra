# Event gateway tests

Existing event gateway container health: **healthy**.

Loopback `/healthz` returned `{"status":"live"}`. Runtime flags show
`SEND_EVENTS=false` and `ENABLE_EXTERNAL_DELIVERY=false`. Complete synthetic
event lifecycle and restart/no-false-gap evidence were not executed in this
non-disruptive run and remain **NOT_TESTED**.

- Service `codestra-telephony-event-gateway-rc4.service` was active.
- Health endpoint `127.0.0.1:9478/healthz` returned `{"status":"live"}`.
- Runtime is a read-only, capability-dropped container with persistent state.
- `SEND_EVENTS=false` and `ENABLE_EXTERNAL_DELIVERY=false` were preserved.
- Asterisk reported zero active channels/calls during the inventory.

Full dial/answer/bridge/hangup lifecycle and restart-without-gap tests were not
run because they require an approved synthetic call window. Status: `PARTIAL`,
production blocked.
