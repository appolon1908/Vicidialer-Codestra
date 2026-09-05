# Bounded disabled-agent provisioning

`POST /v1/agents/provision-disabled` is the only account-creation route. It is
separate from `/v1/agents/sync`, requires the dedicated
`telephony:agent-provision` scope, and accepts only version-2 signed requests.

The route remains unavailable unless both `ENABLE_VICIDIAL_WRITES=true` and
`ENABLE_AGENT_PROVISIONING=true`. Enabling it does not enable agent sync,
campaign provisioning, dialing, call control, lead publication, email, or SMS.

The protected policy file is root-owned, not world-accessible, and binds one
request to an immutable release SHA, authorization reference, expiry,
validated-backup reference, level 9, and the exact disabled agent payload. A
different identity, group, campaign set, role, backup, or payload is rejected.
The policy belongs in the deployment secret/configuration system and must
never be committed with production values.

Creation uses a fixed parameterized database operation. The bootstrap password
is randomly generated on Server B and is not returned or logged. Successful
creation requires an inactive-user readback and records the canonical mapping,
desired state, correlation ID, and redacted audit evidence. Idempotent retries
return the prior result; conflicting identifiers fail closed.

Activation and credential delivery are explicitly outside this route.
