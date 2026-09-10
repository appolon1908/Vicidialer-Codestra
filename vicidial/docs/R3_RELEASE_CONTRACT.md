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

The `production-candidate` profile requires the operational MySQL repository
to pass its connection and schema probes. An in-memory backend returns
`503` from `/ready`, even with valid release metadata and all write switches
disabled. This matches the startup validator. Release metadata must also name
the `production-candidate` build environment; a valid staging or production
build cannot stand in for that candidate. This keeps `/ready` aligned with
the systemd operational verifier.

The memory-backed shadow deployment and isolated container smoke test use
`staging-read-only` explicitly; their readiness does not certify a production
database. The production image default and production Compose profile remain
`production-candidate` and require MySQL configuration.

## Protected API authentication

Every `/v1/*` operation requires the exact authorized source,
service identity, authorization scope, timestamp, nonce, HMAC-SHA256
signature, durable replay rejection, and shared rate limiting.

## Lead publication

Lead publication requires the global write switch, the feature switch,
signed-request authorization, and explicit membership of the target
list in `CODESTRA_ALLOWED_LEAD_LIST_IDS`. An empty allowlist denies all
lead-publication requests.
