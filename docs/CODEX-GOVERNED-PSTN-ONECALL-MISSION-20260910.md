# Codex Mission — Governed PSTN One-Call Production Certification

Date: 2026-09-10

## Objective
Complete the governed one-call production certification for destination `+18496582053` through Middleware → mTLS/private VICIdial edge → restricted adapter → loopback AMI → `outbound-didww` → `didww-out`, then immediately close the dialing gate and preserve evidence.

## Server authority
Primary execution server: `65.21.67.207` (VICIdial/Asterisk/DIDWW runtime).
Middleware source server: `65.109.65.169` (signed HMAC caller to `/v1/calls/originate`).
Do not originate from any other host and do not bypass the governed adapter with raw Asterisk originate commands.

## Canonical source
Repository: `appolon1908-hue/Vicidialer-Codestra`
Required deployed baseline before call: current protected `main`, including merged PR #41 (`f43aa44fa0082eb2bb15be9ec3950ddfd8bc72e1` at mission creation time). Re-resolve `origin/main` immediately before deployment and pin the exact SHA used in evidence.

## Safety invariants
- Exactly one external call is authorized.
- Exact destination: `+18496582053` only.
- Exact campaign/purpose must match the protected external-call policy.
- `recording_requested=false`.
- `max_calls=1`.
- Never retry automatically after `dispatch_unknown` or `submitted_unknown`.
- Do not enable general VICIdial writes, callbacks, SMS, email, AI actions, warm transfer, IVR routing, or unrestricted PSTN dialing.
- Do not expose credentials, HMAC keys, AMI secrets, or certificate private keys in logs, commits, issues, PRs, or evidence.

## Execution plan
1. On `65.21.67.207`, fetch `origin/main`, confirm a clean source checkout, build a release directory for the exact resolved SHA, generate/verify `BUILD_INFO.json`, `SOURCE.COMMIT`, and installed manifest, then atomically point `current` to that release.
2. Regenerate the protected one-call policy so `release_sha` equals the exact deployed SHA, destination is `+18496582053`, dialplan context is `outbound-didww`, prefix is `8`, trunk is `didww-out`, `max_calls=1`, and recording is disabled.
3. Verify the dedicated AMI credential remains loopback-only and can perform only the bounded actions required by the executor. Never print the secret.
4. Restart `codestra-vicidial-restricted-adapter.service` and require `/health`, `/ready`, and `/version` to pass. `/ready` must report `deployment_profile=production-external-one-call`, `release_verified=true`, `external_call_policy=true`, and `production_dialing=true`.
5. Run the corrected external route verifier against live Asterisk. It must accept only the exact resolved DIDWW route and must reject unexpected includes, switches, contexts, applications, priorities, trunks, or caller-ID values.
6. Verify `sip show peer didww-out` is healthy and resolves to DIDWW before dialing.
7. Verify durable external-call state contains zero prior calls for this authorization. If any row exists, STOP and reconcile; do not delete state to force another call.
8. From `65.109.65.169`, create exactly one signed HMAC request to `POST /v1/calls/originate` through the private/mTLS route. Use a fresh unique `operation_id`, matching `Idempotency-Key`, fresh nonce/timestamp, exact required scope, exact authorized campaign, exact destination `+18496582053`, purpose `controlled_test`, and `recording_requested=false`.
9. Submit the request exactly once. If the HTTP result or AMI dispatch outcome is uncertain, STOP and reconcile durable state/Asterisk events before any further action. Never blind-retry.
10. Capture evidence: request correlation/operation IDs, adapter result, durable state row, relevant audit records, Asterisk `OriginateResponse`/channel events, DIDWW/SIP evidence, call disposition if determinable, and timestamps. Redact secrets.
11. Immediately close the dialing window after the attempt, regardless of answer/no-answer/failure: restore the adapter to the normal fail-closed profile and set `ENABLE_PRODUCTION_DIALING=false`. Restart and prove `/ready` no longer authorizes production dialing.
12. Run post-call health checks: adapter, Asterisk, DIDWW peer, failed systemd units, disk, integration health, and recent logs. Existing unrelated alerts must be reported rather than hidden.

## Repository updates required after execution
Commit only non-secret operational corrections, tests, docs, and evidence templates. If runtime drift was required to make the call work, reproduce the same correction in this repository, run the complete CI suite, open a PR, obtain independent approval, and merge only when green. No local-only production fix may remain undocumented.

## Success criteria
- Exact canonical SHA deployed and verified.
- Exact DIDWW route certified.
- Exactly one governed originate request submitted for `+18496582053`.
- Durable idempotency/audit evidence preserved.
- No second call attempt.
- Dialing gate closed immediately afterward.
- Production runtime and Git repository reconciled with no unexplained drift.
