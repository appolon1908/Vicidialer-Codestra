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
