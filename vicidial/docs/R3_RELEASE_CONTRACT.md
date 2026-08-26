# Codestra VICIdial R3 release contract

R3 is a restricted, fail-closed production-candidate release. A
successful deployment does not authorize VICIdial writes, live PSTN
dialing, callback dispatch, lead publication, or external delivery.

## Immutable identity

The full Git SHA, immutable release-directory name, `SOURCE.COMMIT`,
`BUILD_INFO.json.git_sha`, `/version.git_sha`, manifest source SHA,
running application source, and running Python environment must match.
Any mismatch is a deployment failure.

## Canonical operational endpoints

Only `GET /health`, `GET /ready`, and `GET /version` are canonical.
`/healthz` and `/status` are not part of the adapter contract.

## Protected API authentication

Every `/v1/*` operation requires the exact authorized source,
service identity, authorization scope, timestamp, nonce, HMAC-SHA256
signature, durable replay rejection, and shared rate limiting.

## Lead publication

Lead publication requires the global write switch, the feature switch,
signed-request authorization, and explicit membership of the target
list in `CODESTRA_ALLOWED_LEAD_LIST_IDS`. An empty allowlist denies all
lead-publication requests.
