# Hardened restricted-adapter container

These files package only the Codestra VICIdial Restricted Adapter. They do not
containerize Asterisk, VICIdial core, or MariaDB, and they grant no authority to
deploy or enable a live capability.

The image runs as UID/GID `10001`, binds `0.0.0.0:8097` only inside its isolated
network namespace, and publishes production traffic on host loopback
`127.0.0.1:8097`, behind the existing Apache private
mTLS ingress at `10.40.0.2:8443`, whose authorized peer is `10.40.0.1`.
Production mounts only a parameterized MariaDB Unix-socket directory read-only;
it never mounts database files or publishes a database port. Shadow publishes
only `127.0.0.1:18097` and uses the memory backend. Port
9443 is not part of this packaging.

Build locally from the repository root using an immutable merged commit:

```sh
SOURCE_SHA=$(git rev-parse HEAD)
BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
podman build --arch amd64 --file vicidial/docker/Dockerfile \
  --build-arg SOURCE_SHA="$SOURCE_SHA" --build-arg BUILD_DATE="$BUILD_DATE" \
  --tag codestra/vicidial-restricted-adapter:"$SOURCE_SHA" .
```

Compose requires an image digest and explicit secret file paths. All capability
flags are fixed false. Production references the independently managed local
MariaDB but must not be started until the separate production certification and
change approval are complete. Shadow uses the in-memory repository and performs
no VICIdial database access.

The image health check verifies `/health`, `/ready`, and `/version`, including
the image source SHA. The entrypoint additionally rejects an inconsistent
`SOURCE.COMMIT`, release-directory name, `BUILD_INFO.json`, or manifest source
SHA, and refuses startup if any live capability flag is true.

CI builds linux/amd64 without pushing, tests startup/health/restart behavior,
and emits an SPDX SBOM plus an in-toto/SLSA provenance statement as workflow
artifacts. Neither Compose file mounts the Docker socket.

Production Compose requires `VICIDIAL_ADAPTER_IMAGE` to contain the complete
verified release reference, including the permitted GHCR repository and
immutable `sha256` digest. Generate it only with
`vicidial/scripts/validate_deployment_artifact.py`; a tag or digest-only variable is not
accepted.
