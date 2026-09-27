# Internal echo test call — operator runbook

One governed internal call: Middleware asks the restricted VICIdial adapter on
Server B to ring `PJSIP/6901` (user `appolon`, campaign `TEST_SYN`); when the
person on 6901 answers, Asterisk connects them to extension `6000` in context
`codestra-internal-test` (Answer → Echo → Hangup). No external number is dialed,
nothing is recorded, and the authorization allows exactly one call.

This runbook does not authorize the call. It must be run by a human operator
under an approved change ticket (`CHG-…`). Claude/agents only prepare and read.

## Roles

| Role | Does |
|---|---|
| Server B admin (root) | Runs the readiness probe, writes the ViciDial policy, flips the gate, watches Asterisk |
| Middleware operator | Writes the Middleware grant, obtains the Odoo-client token, sends the originate |
| Person on 6901 | Registers a WebRTC/SIP device as 6901, answers, speaks, hears the echo, hangs up |

## 0. Known blockers to clear first

These were found in source on 2026-09-27 and will stop the call if not resolved:

1. **Ingress path.** Middleware requires `VICIDIAL_INTERNAL_CALL_BASE_URL` to be
   an HTTPS *origin with no path* and sends `/v1/calls/internal/...` to it. The
   Apache include in this repo only proxies `/restricted-vicidial/…`, and its
   allow-list does not name `calls/internal`. Either the live vhost forwards
   `/v1/calls/internal/` to `127.0.0.1:8097` with the same mTLS + `Require ip`
   guard, or this is a source change to make first. The probe reports this as
   `ingress_internal_call_route`.
2. **Identity must be active for the window.** The adapter's pre-dial check
   (`internal_call_identity_ready`) requires `appolon` active=`Y`, level 8, group
   `WEBTEST_SYN` allowed exactly `TEST_SYN`, phone 6901 active=`Y`, and campaign
   `TEST_SYN` active=`Y`. The steady-state contract keeps `appolon` at
   `active=N`, so activating it is part of the approved change.
3. **Middleware's `/platform/v1/calls` read API will not show this call.** Its
   lifecycle table is only written by the legacy `/calls/originate` handler, and
   the projection skips unknown calls. Use the request status route below and
   the Server B lifecycle store instead.
4. **`timeout` is not emitted** (no authoritative source). An unanswered call
   ends as `no_answer`, `canceled`, or `failed`, never `timeout`.

## 1. Readiness (T-1 day, read-only)

On Server B, as root, from the deployed release or a checkout of this branch:

```bash
sudo python3 scripts/internal_call_readiness.py
```

On the Middleware host (or with its env file and grant):

```bash
python3 scripts/internal_call_readiness.py --middleware-only --middleware-env-file /path/to/middleware.env
```

Proceed only when both end with `READINESS=READY_PENDING_MANUAL_CONFIRMATION`
and the two `…cross_host_fingerprint` lines show the **same**
`authorization_reference` and `principal_sha256`. Then confirm the three
`MANUAL` lines (backup/restore, rollback, change ticket) in the change record.

## 2. The two one-call authorizations

Both are single-use and short-lived. They must name the same person and ticket.

**Server B — `CODESTRA_INTERNAL_CALL_POLICY_FILE`** (root-owned, group
`codestra-adapter`, mode `0440`, no symlink):

```json
{
  "schema_version": "1.0",
  "release_sha": "<adapter /version git_sha>",
  "authorization_reference": "CHG-<ticket>",
  "expires_at": "<UTC, end of window>",
  "tenant_id": "<tenant>",
  "subject": "<Keycloak sub of the caller>",
  "employee_id": "<employee>",
  "username": "appolon",
  "extension": "6901",
  "campaign": "TEST_SYN",
  "destination_alias": "internal:TEST_ECHO",
  "destination_extension": "6000",
  "dialplan_context": "codestra-internal-test",
  "internal_only": true,
  "external_dialing": false,
  "recording": false,
  "max_calls": 1,
  "route_sha256": "f6dd114fa050a73fdbcf8fbfc72bdb8f08b9ddb462231b99cf81e4a7f3e5afa6"
}
```

`route_sha256` is the digest of the approved Answer/Echo/Hangup route; the probe
recomputes it from the live dialplan and fails if they differ.

**Middleware — its own `CODESTRA_INTERNAL_CALL_POLICY_FILE`** (root-owned, no
group/other write, ≤ 16 KiB, window ≤ 1 hour):

```json
{
  "authorization_reference": "CHG-<same ticket>",
  "principal": {
    "tenant_id": "<same tenant>", "subject": "<same sub>", "employee_id": "<same employee>",
    "campaign_id": "TEST_SYN", "business_unit": "<bu>", "extension": "6901"
  },
  "destination": "internal:TEST_ECHO",
  "caller_id": "<E.164 caller id>",
  "lead_id": <Odoo crm.lead id>,
  "not_before": "<UTC>",
  "expires_at": "<UTC, ≤ 1h after not_before>",
  "source_sha": "<Middleware APP_SOURCE_SHA>",
  "internal_only": true,
  "external_dialing": false,
  "max_calls": 1
}
```

## 3. Call window

1. **Server B:** set `ENABLE_INTERNAL_TEST_CALLING=true` in
   `/etc/codestra/vicidial-restricted-adapter.env`, restart
   `codestra-vicidial-restricted-adapter`, and confirm
   `curl -s http://127.0.0.1:8097/ready` shows `"status":"ready"`,
   `"internal_test_calling":true`, `"production_dialing":false`.
2. **Person on 6901:** register the device; the admin confirms with
   `asterisk -rx "pjsip show aor 6901"` (exactly one contact, `Avail`).
3. **Admin:** start watching, in separate terminals:
   `asterisk -rvvv` and
   `journalctl -fu codestra-vicidial-restricted-adapter`.
4. **Middleware operator:** obtain a short-lived (≤ 300 s) Keycloak token for
   client `odoo-integration` with scope `telephony.calls.originate` and claims
   `tenant_id`, `sub`, `employee_id`, `campaign_id=TEST_SYN`, `business_unit`,
   `extension=6901` matching the grant. Then send exactly one request:

   ```bash
   curl -sS -X POST "$MIDDLEWARE/v1/telephony/calls/originate" -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $KEY" -H "X-Correlation-ID: $CORRELATION" -H "Content-Type: application/json" --data @originate.json
   ```

   `originate.json` (every value must equal the grant / token):

   ```json
   {
     "employee_id": "<employee>", "campaign": "TEST_SYN", "business_unit": "<bu>",
     "destination": "internal:TEST_ECHO", "destination_class": "internal_test",
     "destination_country": "<ISO-2>", "destination_timezone": "<IANA tz>",
     "caller_id": "<same E.164>", "lead_model": "crm.lead", "lead_id": <same id>,
     "recording_requested": false, "idempotency_key": "<same as $KEY>"
   }
   ```

   Expect `202` with `operation_id`, `dialing: "attempting"`, and a
   `status_url`. `200` with `"dialing":"blocked"` means the grant was not
   loaded; stop and re-run readiness. **Never** resend with a new key — a retry
   must reuse the same key (it returns the same operation).

5. **Person on 6901:** the device rings; answer, speak, confirm the echo, hang
   up after ~10 s.

## 4. Expected evidence

Lifecycle events for the call, in order (from `codestra.vicidial.call.lifecycle.*`):

`ringing` → `answered` → `connected` → `hangup` → `completed`

(`created`/`offered`/`dialing` may appear first.) Not answering produces
`no_answer`/`canceled`/`failed` instead — record it; do **not** place a second call.

Read back:

- Middleware: `GET $MIDDLEWARE/v1/telephony/calls/requests/<operation_id>`
  (scope `telephony.calls.read`) → `call_state` terminal, `external_dialing:false`.
- Server B, read-only — the channel id is `codestra-` + first 32 hex of
  `sha256(operation_id)`:

  ```bash
  sqlite3 "file:/var/lib/codestra-vicidial/ami-events.sqlite3?mode=ro" "SELECT c.state,c.answered,c.terminal,c.ended_at FROM calls c JOIN channels ch ON ch.call_key=c.call_key WHERE ch.uniqueid='codestra-<32hex>'"
  ```

  (use the `AMI_EVENT_STATE_DB` path the probe printed).
- Odoo: exactly one call history entry on the granted `crm.lead`.

## 5. Abort

- Normal: `POST $MIDDLEWARE/v1/telephony/calls/requests/<operation_id>/hangup`
  (scope `telephony.calls.hangup`, same `X-Correlation-ID`, body
  `{"idempotency_key": "...", "expected_version": <n>, "reason": "operator abort"}`).
- Emergency (admin): `asterisk -rx "channel request hangup <PJSIP/6901-…>"`.
- Either way, continue with section 6.

## 6. Close the window (always)

1. Server B: `ENABLE_INTERNAL_TEST_CALLING=false`, restart the adapter, confirm
   `/ready` shows `"internal_test_calling":false`.
2. Return `appolon` to its steady state (`active=N`) if it was activated.
3. Remove or let expire both policy files.
4. Re-run the probe; `one_call_unused` must now be `FAIL` (consumed) — this is
   expected and blocks any further call until an administrator reconciles.
5. Record in the change ticket: operation_id, correlation id, Asterisk channel
   id, event sequence, terminal state, Odoo entry id, and both probe outputs.

```text
PRODUCTION_CHANGES=<gate toggled / identity activated, then reverted>
LIVE_CALLS_PLACED=<0|1>
```
