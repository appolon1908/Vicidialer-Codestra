# Server B source-release contract

This contract requires a verified gzip tar rooted at the contents of
`vicidial/`, a fresh release-local virtual environment, and an adapter-only
activation/rollback transaction. It does not establish that the installed host
operator implements those requirements.

The installed operator inspected on 2026-09-10 had SHA-256
`015fda29ada5eb11d71a6177876fdf102c7fe83932dda91772e09994c67501ee`.
Its `deploy` action reported no mutation and its `restart-controlled` action
covered three units. Those actions are not evidence of this contract's bounded
release transition. Inspect the current operator again before integration.

The source-controlled [prepared-release transaction](PREPARED_RELEASE_TRANSACTION.md)
provides the adapter-only pointer switch, durable recovery journal, effective
process/readiness checks and predecessor rollback. Administrator preparation of
the final release directory and fresh venv remains a separate prerequisite.
The transaction must pass protected review, publication and installation before
it can be invoked on a host; this document does not authorize a deployment.

The archive contains `pyproject.toml`, `src/`, `scripts/`, `config/`, `ops/`,
dependency locks, tests and docs, plus `SOURCE_SHA`, `SHA256SUMS`, and
`SOURCE_RELEASE_MANIFEST.json`. Git data, secrets, credentials, certificates,
environment files, caches, reports, evidence and databases are excluded. Tar
entries are sorted, normalized to root ownership and epoch timestamps.

The protected workflow builds the payload from the exact protected source SHA,
records the workflow SHA separately, and signs the archive with keyless
Sigstore. Staging verifies the outer SHA-256, embedded source SHA, per-file
checksums, manifest digest, signature identity, protected ancestry and rollback
state before invoking an approved installed transaction. Installed files are root-owned and
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

## Administrator deployment prerequisite: venv and effective predecessor

Do not deploy while the effective process and `current` disagree. Before approving
an existing installer, inspect its actual deployed source/hash and argv contract;
a historical local script is not current operator authority. The source archive
does not authorize `stage`, `deploy`, a restart, or a symlink change.

The existing administrator procedure must create a fresh venv at its final
release-local path, install pinned dependencies and the verified selected package,
and verify every console-script shebang and installed import path against that
release. Do not copy/move a venv or upgrade the predecessor in place. Rehearse this
in disposable directories; hash predecessor files before and after and require
no changes. Resolve the discrepancy inside the reviewed transaction, never with a
standalone `ln -sfn` or broad restart. Only the affected adapter unit may restart.

Record the actual process UID/GID, interpreter, working directory, loaded module
identity, distribution versions, source and installed manifests, immutable dependency
bytes, protected configuration references and durable-state compatibility. Readiness
and a directory named after a SHA are insufficient. Capture an independently usable
predecessor and the configuration/state it needs, validate backup encryption and
off-server/isolated-restore evidence under the current freshness policy, and test
rollback read-back. A failure must remain a failure with delivery disabled.

If the installed operator lacks these properties, its owner must submit the bounded
correction through the existing operator source/review/deployment process. Do not
hot-patch the host or introduce a replacement installer in packaging PR #28.


## Selected correction and publication binding

The selected runtime payload is `9ac8ef4840f78ba4ad9b816e4e409298505103ce`,
which contains PR #29's startup-policy substitution repair. The publication
confirmation is `PUBLISH_SERVER_B_SOURCE_RELEASE_9AC8EF48`. The previously
selected 75f48ffe source remains historical evidence and is not this payload.

The workflow/builder SHA is the protected merge of PR #28 (or a separately
reviewed successor); it is recorded separately from the runtime source SHA.
Both the signed archive manifest and signed provenance bind the builder SHA.
Provenance also binds source repository/SHA, archive name/digest, the exact
workflow identity and run/attempt. Verification must use the observed protected
run's head SHA and run ID/attempt, never values trusted solely from a downloaded
manifest. After downloading, verify both Sigstore bundles and rerun the archive
and provenance semantic checks with those independent expected values.

The selected archive's installed package is exercised outside the checkout,
including the canonical runtime validator, startup-policy substitution rejection
and same-call cleanup after new-start authority expiry. These are isolated
contract tests, not browser/SIP/audio or production deployment certification.
