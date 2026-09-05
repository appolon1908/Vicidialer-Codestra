# Server B source-release contract

Server B's existing restricted operator consumes a gzip tar rooted at the
contents of `vicidial/`. It installs that Python project into a new
`/opt/codestra/vicidial-restricted-adapter/releases/<source-sha>` directory,
reuses the validated virtual environment, atomically changes `current`, and
restarts only `codestra-vicidial-restricted-adapter.service`. Failed validation
restores the effective previously running release.

The archive contains `pyproject.toml`, `src/`, `scripts/`, `config/`, `ops/`,
dependency locks, tests and docs, plus `SOURCE_SHA`, `SHA256SUMS`, and
`SOURCE_RELEASE_MANIFEST.json`. Git data, secrets, credentials, certificates,
environment files, caches, reports, evidence and databases are excluded. Tar
entries are sorted, normalized to root ownership and epoch timestamps.

The protected workflow builds the payload from the exact protected source SHA,
records the workflow SHA separately, and signs the archive with keyless
Sigstore. Staging verifies the outer SHA-256, embedded source SHA, per-file
checksums, manifest digest, signature identity, protected ancestry and rollback
state before invoking the existing operator. Installed files are root-owned and
not group/world writable; the service remains `codestra-adapter` with the
existing root-managed environment and credentials.

`STAGING_READ_ONLY` remains mandatory. Calling, provisioning, database writes,
PSTN, callbacks, transfers, SMS and email remain disabled. The current symlink
is not alone rollback evidence: any mismatch between it and the running
executable blocks activation until the operator records the effective process
release as the rollback target.

## Canonical source archive permissions

Every archive directory is root-owned mode 0755. Ordinary source files and generated
`SOURCE_SHA`, `SHA256SUMS`, and `SOURCE_RELEASE_MANIFEST.json` are mode 0644.
Executable source files are mode 0755 only when the checked-out source commit's
Git tree records mode 100755; filesystem chmod, umask, setuid/setgid and extra write
bits do not confer executable authority. Builds require that source Git checkout;
untracked payload files fail closed. Generated Python egg-info is excluded along
with caches. The outer archive and its generated non-secret metadata sidecar are
mode 0644. No runtime credential or policy file permissions are changed.

The subprocess regression builds identical source/builder revisions under umasks
0022, 0027 and 0077, verifies identical archive bytes and member modes, verifies
checksums, and opens extracted files as a non-root process. It also deliberately
changes working-tree modes after committing executable intent to prove Git metadata
is authoritative. These tests establish source-archive readability; live service
UID/GID, environment, loaded package and rollback identity still require preflight.
