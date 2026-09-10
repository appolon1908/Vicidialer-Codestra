# Prepared adapter release transaction

Issue #11 needs an actual adapter activation and rollback transaction. A source
archive, a staged marker, or a successful no-op operator action cannot establish
that the selected release is running.

`scripts/release_transaction.py` is a Python 3.11 root-only controller for one
already prepared release. Its only CLI argument is `preflight`, `activate`, or
`rollback`. It has no caller-selected path, unit, command or source argument.
It only restarts `codestra-vicidial-restricted-adapter.service`.

## Scope and prerequisites

This controller does not download an archive, create a venv, install dependencies,
migrate a database, restore application data, or enable calling. The administrator
must first prepare the final immutable release directory through a reviewed
offline preparation procedure. Create a fresh venv at that final path with local
interpreter bytes; do not copy a predecessor venv or retain console entrypoints
bound to another release. The independently verified source artifact, pinned
dependency bytes, configuration and durable-state compatibility are prerequisites.

The controller requires both the effective process and HTTP readiness to use
`staging-read-only`, the isolated `memory` backend, and disabled write/delivery
capabilities. The current pointer, process working directory, effective interpreter
and HTTP source identities must agree. Asterisk must report zero active channels
and calls. A production external one-call profile is rejected.

The controller is source code pending protected review and administrator
integration. Do not hot-patch the installed operator, deploy a feature branch, or
use this controller to change an active calling profile. Issue #11 stays open
until the separate runtime provisioning, event delivery, restore and authorized
calling evidence is complete.

## Fixed authority and immutable layout

The code uses these fixed locations:

| Location | Role |
| --- | --- |
| `/opt/codestra/vicidial-restricted-adapter/releases/<sha>` | Candidate and retained predecessor |
| `/opt/codestra/vicidial-restricted-adapter/current` | Atomically replaced release pointer |
| `/etc/codestra/vicidial-release/plan.json` | Root-approved transaction authority |
| `/etc/codestra/vicidial-release/verified-release.json` | Independent preparation verification |
| `/etc/codestra/vicidial-release/verified-backup.json` | Independent recovery verification |
| `/etc/codestra/vicidial-restricted-adapter.env` | Existing root-managed environment |
| `/var/lib/codestra-vicidial-release/journal.json` | Durable transaction and recovery intent |
| `/var/lib/codestra-vicidial-release/audit/*.json` | Retained immutable transaction state events |

Authority, release files and their containing directories must be root-owned and
not group/world writable. State is created with mode 0700; journal and lock files
use mode 0600. Release symlinks must stay inside their own release. The controller
checks file ownership before calculating the canonical installed manifest, then
checks `SOURCE.COMMIT`, `BUILD_INFO.json`, manifest source and directory identity.
Excluded cache directories and `.pyc`/`.pyo` files are rejected before executing
any prepared interpreter. Prepare dependencies without bytecode compilation; the
controller disables bytecode generation for its own imports as well.
It probes the prepared interpreter as the service UID/GID with a minimal
environment and verifies its prefix, source import path and Python version.

The exact plan fields are:

| Field | Required value |
| --- | --- |
| `schema_version` | Integer 1 |
| `authorization_reference` | Recorded administrator change reference matching `CHG-[A-Z0-9-]{10,80}` |
| `expires_at` | Future ISO timestamp including timezone |
| `source_sha`, `previous_sha` | Different full 40-character source commits |
| `manifest_sha256`, `previous_manifest_sha256` | Approved raw installed-manifest digests |
| `unit_sha256` | Digest of effective `systemctl cat` output for the one adapter unit |
| `environment_sha256` | Digest of the existing root-managed environment file |
| `release_evidence_sha256`, `backup_evidence_sha256` | Raw digests of the independent reports |

Do not create success attestations merely to satisfy this schema. The release
report must bind `source_sha`, `installed_manifest_sha256` and
`artifact_sha256`, with independently established boolean results for
`signature_verified`, `provenance_verified`, `protected_ancestry_verified`,
`offline_dependencies_verified`, `release_local_venv_verified`, and
`state_compatibility_verified`. The controller binds these reports by digest;
it does not itself perform Sigstore verification or dependency installation.

The backup report must bind `previous_sha` and a fresh `expires_at`, with verified
encrypted backup, off-host copy, isolated restore, configuration restore and
state restore results named `encrypted_backup_verified`,
`off_host_copy_verified`, `isolated_restore_verified`,
`configuration_restore_verified` and `state_restore_verified`. The isolated
restore procedure must not create or modify a database on the live host.

## Transaction and recovery behavior

An exclusive, nonblocking file lock serializes actions. Preflight rejects a
systemd unit with pending or unknown reload state, so on-disk
unit digests cannot authorize a stale manager configuration. It verifies both
prepared releases, authority, configuration, effective predecessor, readiness and
Asterisk inactivity. It does not change the pointer or restart a service.

Activation fsyncs recovery intent before atomically replacing the pointer,
rechecks configuration, restarts the one adapter unit and verifies process plus
HTTP read-back. It rechecks configuration after runtime read-back. Only then
does it record commitment. Repeating the same committed
plan verifies the release and runtime without restarting again.

Each state change first publishes a root-private, fsynced audit event with its
transaction ID, authorization, timestamp and outcome, then replaces the recovery
journal. Audit files are created atomically without overwriting or deleting prior
events. Audit storage failure blocks initial mutation or causes recovery after a
candidate start. Starting a later transaction preserves earlier completion records.

A failed activation attempts to restore and verify the retained predecessor.
It still exits unsuccessfully when that recovery succeeds. Failed recovery
retains `recovery_failed` intent for explicit intervention. An interrupted action
blocks another activation until explicit rollback resolves its journal.

Rollback uses the recorded plan rather than a replacement plan file and permits
recovery after authorization expiry. It verifies the retained predecessor and
unchanged configuration; it never depends on the failed candidate's bytes still
existing. An unexpected third release or configuration drift stops recovery
without overwriting someone else's deployment. Repeated successful rollback
verifies the predecessor again without a second restart.

Rollback restores the release pointer and service, not database history or
configuration files. It therefore requires prior state compatibility and
independently rehearsed recovery evidence. No campaign, lead, call or event data
is deleted by this controller.

## Validation limits

`vicidial/tests/test_release_transaction.py` uses real temporary release trees,
canonical manifests, symlink replacement, fsync and lock/journal operations.
Systemd, Asterisk, interpreter execution and HTTP are simulated boundaries.
Tests cover rejected effectful profiles, damaged artifacts, failed candidate
start, failed rollback, interruption, expired authorization, missing candidate
files, idempotent retries and concurrent deployment exclusion.

These are source regression results. They do not certify a live restart,
administrator authorization, backup restoration, SIP/audio, provisioning, or
production delivery. Attach the exact tested commit, command, UTC timestamp and
CI run when publishing evidence, and retain host evidence separately.
