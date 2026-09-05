# Bounded internal calling

The restricted adapter exposes three machine-authenticated Server B operations:

- `POST /v1/calls/internal/originate`
- `GET /v1/calls/internal/{operation_id}`
- `POST /v1/calls/internal/{operation_id}/hangup`

They are disabled unless `ENABLE_INTERNAL_TEST_CALLING=true`, verified release
metadata is loaded, and a private root-owned `CODESTRA_INTERNAL_CALL_POLICY_FILE`
binds the exact tenant, subject, employee, authorization reference, expiry,
`appolon`, extension `6901`, campaign `TEST_SYN`, alias
`internal:TEST_ECHO`, destination `6000`, and the dedicated
`codestra-internal-test` dialplan context. The policy fixes internal-only,
no-external-dialing, no-recording, and one-call limits.

The caller cannot supply an Asterisk channel, context, application, trunk, SIP
URI, or raw destination. The executor writes immutable intent before AMI
origination and derives a deterministic Asterisk ChannelId from the durable
operation ID. An uncertain dispatch is retained as `dispatch_unknown` and is
never sent again. Readback joins that identity to the existing lifecycle store.
Hangup is permitted only when the lifecycle store identifies an active channel
for that exact call, and its dispatch is likewise idempotent and fail-closed.

The production transport connects only to loopback AMI using a protected
credential file. Credential values are never returned or logged. These source
contracts do not prove that `6000` is installed, `6901` is registered, or any
call occurred; those require sanitized runtime inspection and a human test.

## Additional execution gates

The endpoints and scopes above remain the contract shared with Middleware PR
#145. `ENABLE_INTERNAL_TEST_CALLING` defaults to `false`; setting it to `true`
outside `CODESTRA_ADAPTER_DEPLOYMENT=production-internal-test` is rejected by the
application, entrypoint and runtime validator. This profile requires the live
MySQL repository and verified release metadata. All general write, provisioning,
live-call-control, SMS, email and production-dialing flags remain false.
Readiness reports `internal_test_calling` separately and checks the loaded,
current root-owned policy. A healthy read-only adapter is not call certification.

The administrator must supply the existing paths plus the following evidence:

- `CODESTRA_INTERNAL_CALL_POLICY_FILE`: absolute, root-owned protected JSON in a
  root-owned directory; no adapter write permission. Policy is reopened without
  following symlinks on every request, including readback and hangup.
- `CODESTRA_INTERNAL_CALL_STATE`: durable execution database, retained across
  restarts. Never delete or rotate it to bypass the one-call limit. Unbound legacy
  intents are rejected and require administrator reconciliation.
- `CODESTRA_AMI_LIFECYCLE_STATE`: the actual AMI gateway database, with bounded
  read-only access to its database/WAL files. This change does not grant access.
- `CODESTRA_AMI_CREDENTIAL_FILE`: protected root-owned JSON containing the
  separately reviewed loopback AMI credential. Neither credential values nor
  arbitrary AMI parameters are accepted through HTTP.
- `CODESTRA_ADAPTER_STATE`: the existing adapter audit database; requested,
  accepted, duplicate, denied and failed/uncertain outcomes are recorded there.

The policy additionally requires `route_sha256`. Before each new originate,
`ShowDialPlan` must return exactly extension `6000` in
`codestra-internal-test`, priorities `1`, `2`, `3`, applications `Answer`, `Echo`,
`Hangup`, and empty `AppData`. Includes, patterns, switches, other contexts and
other applications fail closed. The administrator must inspect the actual route
and sign off its isolation before supplying the hash. The hash is SHA-256 of
UTF-8 JSON with sorted keys and separators `(',', ':')`, over priority-ordered
objects with exactly `Context`, `Extension`, `Priority`, `Application`, `AppData`
(all string values). A hash copied from a sample is not live route evidence.

The live read-only SQL check requires appolon active, level 8, group WEBTEST_SYN,
exact campaign membership TEST_SYN, and its `phone_login` bound to active 6901,
with matching dialplan number and active TEST_SYN campaign. This code does not
activate those resources. Tenant, subject and employee authorization remain in
the protected policy; SIP registration, browser enrollment, backup/restore,
route isolation and the human test are separate administrator prerequisites.

## Retries and authoritative results

The intent is committed before AMI dispatch. A timeout consumes the one-call
budget and remains uncertain; neither an operation retry nor a new operation ID
can redial. Concurrent hangup requests must win a single atomic claim. Hangup
uses the stored unique ID, after matching both gateway evidence and live AMI
Status for PJSIP/6901. No channel name, regex, trunk or dialplan input is supplied
by Middleware. The receive-only AMI gateway is unchanged.

Readback verifies the event-store tenant, subject, agent, extension, campaign and
unique ID. A terminal flag alone is insufficient: a matching terminal outbox
event and end timestamp are required. The response includes the gateway event
ID and bounded duration/cause evidence. AMI success only means accepted; neither
originate nor hangup acknowledgement proves call completion or audio.

Turning off the originate gate keeps same-call hangup/readback available in the
installed dedicated profile. Expiry, policy removal or replacement also denies
cleanup through this API; administrator recovery must remain available for that
case. No automated replacement call or broad fallback is permitted.

A container must share an explicitly reviewed transport to Asterisk's loopback
AMI and MariaDB. The existing bridge-network deployment does not establish that
path. No host-network change, AMI grant, account activation, deployment or live
call is performed by this source change. Protected artifacts and renewed review
of the integrated head precede administrator deployment and human audio testing.

AMI protocol references: [ShowDialPlan](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/ShowDialPlan/),
[Originate](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Originate/),
[Status](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Status/),
[Hangup](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Hangup/).
