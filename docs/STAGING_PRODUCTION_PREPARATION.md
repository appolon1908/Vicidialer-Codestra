# VICIdial staging and production preparation

## Status and immutable candidate

This package prepares configuration; it does not deploy, certify a host, enable
calling, create credentials or grant production approval. The source repository
remains `appolon1908-hue/Vicidialer-Codestra`. The existing Server B operator remains
the runtime authority. Do not install VICIdial on Server A or silently replace the
Server B systemd installation with Docker.

The checked-in `.codestra/deployment-candidate.json` was copied from the actual
`deploy-readiness-manifest.json` produced by Actions run `34129396370`:

- Source: `49a9568fb9b140b85cc8cd859b40145031242475` (merged PR #10).
- Source tree: `93276b021a514a127088f69b0a8b74604aab4584`.
- Image: `ghcr.io/appolon1908-hue/vicidialer-codestra@sha256:6f18414e36313ce2881c973b07394da99153ffd54ad4ec300bfd788d775695ee`.
- Evidence artifact ID: `10021472655`.
- Downloaded evidence ZIP SHA-256: `3c9c39047735dcbb97405cc5ede154704f1867ed76ae6a6b1b5f4e279949f53f`.

That artifact's manifest explicitly says runtime deployment and external effects
are **not authorized**. This inventory is not a substitute for independently
verifying the image signature, OCI subject, source identity, SBOM, provenance and
vulnerability gates. Do not reuse this digest as evidence for a later source SHA.
The preparation workflow's `PREPARATION_SHA` identifies the configuration code;
the manifest's `source_sha` identifies the separately built runtime image.

## Generate both environment packages

From a clean checkout of the preparation source:

```sh
python -m unittest discover -s tests -p test_prepare_vicidial_environments.py -v
python scripts/prepare_vicidial_environments.py \
  --manifest .codestra/deployment-candidate.json \
  --output ../vicidial-prepared
(cd ../vicidial-prepared && sha256sum --check SHA256SUMS)
```

The output directory must not already exist; its parent must exist. The command
makes no network requests and runs no Docker, SSH, database or provider commands.
It emits `staging.compose.json`, `production.env.example`, `deployment-plan.json`
and checksums. All 15 activation flags, including internal test calling and
single-agent provisioning, remain explicitly false. An invalid manifest fails
before output creation. Existing operator packages are never overwritten.

The new **VICIdial staging and production preparation** workflow runs the same
regression tests and a Compose model validation, then uploads the package. It has
only `contents: read`, no production secrets, no deployment environment and no
host mutation steps. A green preparation workflow is not staging certification.

## Staging phase 1: isolated API smoke test

The generated staging Compose model uses the pinned image, a unique project and
state volume, non-root UID/GID 10001, a read-only root filesystem, dropped
capabilities, resource limits and `network_mode: none`. It has **no host ports**,
no production database socket, no host network and no provider egress.
`pull_policy: never` requires the operator to separately obtain and verify the
exact image before starting anything. The image's own health check observes
`/health`, `/ready` and the exact `/version` source SHA inside the container.

The staging host remains unset until an isolated target is actually verified.
`VICIDIAL_STAGING_HMAC_FILE` must refer to a dedicated synthetic staging secret,
not a copied production credential. Its host ownership/mode must allow UID/GID
10001 to read it without making it world-readable. Do not store its contents in
Git, the prepared package, logs or workflow artifacts. The preparation workflow
uses a deliberately nonexistent path only to validate the Compose model and does
not launch a container or provision a credential.

The backend is intentionally `memory`: this tests packaging and API behavior only.
A memory-backed `/ready` response cannot prove MariaDB, Middleware, Odoo, Keycloak,
private mTLS, durable business persistence or live telephony readiness. Network
isolation also deliberately prevents end-to-end integration. Before later
integration certification, review a separate isolated setup with synthetic data,
separate least-privilege identities, approved private routing and explicit egress
allowlisting. No customer data, real restore, production DB or carrier is allowed
in this initial stage. No calling, SMS, email or n8n activation is included.

## Production preparation and source publication

`production.env.example` is a **reference for the existing container contract**,
not permission to install a container on Server B. Its secret and MariaDB socket
paths are empty on purpose. Do not source it into or overwrite the running
systemd service configuration. Use the existing reviewed operator and verified
installer-compatible source archive where the host is systemd-managed.

The publication workflow formerly selected source `9ac8ef48...` unconditionally.
It now requires a full `source_sha` input and the exact confirmation
`PUBLISH_SERVER_B_SOURCE_RELEASE`. It accepts only an ancestor of the immutable
protected-main workflow SHA, validates this before source checkout, runs the
selected source's full test suites plus installer compatibility tests, builds a
deterministic archive and retains archive/provenance signing and verification.
It publishes artifacts only; it does not deploy or change Server B.

After independent review and merge of the workflow update, the authorized
repository operator can request the **current candidate** source archive with:

```sh
gh workflow run publish-server-b-source-release.yml \
  --repo appolon1908-hue/Vicidialer-Codestra --ref main \
  -f source_sha=49a9568fb9b140b85cc8cd859b40145031242475 \
  -f confirmation=PUBLISH_SERVER_B_SOURCE_RELEASE
```

This command has not been executed as part of preparation. The historical signed
archive for `9ac8ef48...` does not certify the candidate above. Verify the newly
published archive, checksums, signatures, builder SHA, run/attempt and source
bindings before presenting it to the existing Server B operator. Future runtime
code changes require their own reviewed source, new image digest and fresh
matching evidence; update the manifest through a reviewed PR or pass the new
verified workflow manifest explicitly to the preparation command.

## Gates still required before production deployment

`deployment-plan.json` intentionally initializes every gate to `NOT_VERIFIED` and
runtime certification to `NOT_RUN`. It records no fabricated host, archive digest,
backup result, approval or production-ready status. Required evidence includes:

1. Independent exact-source review, source/container CI and signed release evidence.
2. Approved isolated staging host, synthetic integration certification and rollback.
3. Server B inventory and source/runtime identity; local-only MariaDB and grants.
4. Private Middleware mTLS (`10.40.0.1` -> `10.40.0.2:8443` -> `127.0.0.1:8097`).
5. Backup checksums, isolated restore proof, monitoring/reconciliation and rollback.
6. Protected production change authorization for the exact source/artifact tuple.

All state-changing deployment work requires a checkpoint and the reviewed narrow
operator. Do not create an unprotected GitHub environment and treat its name as
an approval gate; repository administrators must verify the available protection
rules and independent-review capability for this private repository. This change
does not create environments, add secrets, modify branch rules, apply firewalls,
register extensions, originate calls or send notifications. Deployment and
customer-traffic activation remain separate decisions.

## Evidence references

- Actions evidence: https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/34129396370
- Docker network isolation and Compose services: https://docs.docker.com/reference/compose-file/services/
- GitHub environment protection: https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment
