# VICIdial R3 dedicated-repository consolidation mission

## Objective

Make `appolon1908-hue/Vicidialer-Codestra` the clean, dedicated source-control home for all current Codestra VICIdial work without deploying or enabling any live capability.

This branch starts from dedicated-repository `main` at:

```text
1c84e8bec7e56288adb87c96f292362d33547cff
```

The original migration imported the monorepo VICIdial branch at:

```text
SOURCE_BASE=ccb4ec0d5218501d724035da6be3540319c7513b
FILTERED_BASE=43f823079bee5e68be2e9194d6a6a17d893141f0
```

The current monorepo source candidate to reconcile is:

```text
SOURCE_REPOSITORY=appolon1908-hue/codestra-production-platform
SOURCE_PR=111
SOURCE_HEAD=bf4b04c8f0e0bcceb54edad33aaf51dfd5379a4f
SOURCE_BASE_BRANCH=release/production-activation
SOURCE_BASE_SHA=f3a16308194378a7b1580e04943dc98d64619077
```

## Import boundary

Reconcile the VICIdial-only delta from `ccb4ec0...` through `bf4b04c...`. Do not import unrelated shared-PostgreSQL files.

Required source paths include:

```text
.github/workflows/vicidial-adapter-container.yml
vicidial/scripts/build_host_release.py
vicidial/scripts/verify_installed_manifest.py
vicidial/scripts/verify_oci_export.py
vicidial/src/codestra_vicidial/repository.py
vicidial/tests/test_phase3a_release_network.py
vicidial/tests/test_phase3c_domains.py
vicidial/tests/test_phase3d_container.py
vicidial/tests/test_verify_oci_export.py
```

Preserve dedicated-repository improvements already present on `main`, especially:

- separate runnable and attested container builds;
- active workflows under `.github/workflows/`;
- historical workflow copies under `ci/workflow-history/`;
- deterministic installed-manifest exclusion of runtime bytecode/cache files;
- `MIGRATION_MANIFEST.md` and dedicated migration documentation.

Do not blindly replace a dedicated file when it contains post-extraction fixes. Perform a three-way semantic reconciliation against the source migration base.

## Current exact-head review corrections

In addition to importing the source delta, close the two current Codex P2 findings:

1. Campaign adoption proof must bind `hopper_level` and `auto_dial_level`.
   - Select both fields in the adoption snapshot.
   - Include both values in the observed proof hash.
   - Include both in the conditional mutation predicate.
   - Require exactly one affected row.
   - Add regressions proving concurrent changes to either field block adoption and cannot be overwritten.

2. OCI export verification must require `index.json.schemaVersion == 2`.
   - Reject missing, non-integer, and unsupported schema versions.
   - Validate before processing root descriptors.
   - Add negative tests for missing and wrong schema versions.

Retain all prior OCI verification controls:

- mandatory `oci-layout` with `imageLayoutVersion=1.0.0`;
- descriptor digest and size validation;
- manifest/config/blob authentication;
- layer decompression and rootfs diff-ID verification;
- exact tested-image identity;
- exact source-revision label.

## Repository and branch cleanup

Create a documented, non-destructive branch policy:

```text
main
  <- short-lived feature/fix/chore branch
  <- draft PR
  <- exact-head CI
  <- Codex review
  <- independent approval
  <- protected merge
```

Add `docs/BRANCH_POLICY.md` and `docs/BRANCH_CLEANUP_20260827.md` that classify every existing remote branch as one of:

- canonical;
- active;
- merged deletion candidate;
- historical lineage retained;
- unresolved and not safe to delete.

Do not delete or rewrite any branch in this implementation mission. A branch may be listed as a deletion candidate only when Git proves it is fully reachable from `main`, has no open PR, and is not required by a retained tag or migration mapping.

Do not create long-lived `dev`, `staging`, or `production` branches. The protected release identity is a reviewed merged SHA and immutable artifact digest, not a mutable environment branch.

## Required validation

Run from a fresh Python 3.11 environment and preserve exact counts:

```text
PYTHON_COMPILATION
GIT_DIFF_CHECK
WORKFLOW_YAML_PARSE
SOURCE_SECRET_SCAN
PHASE_3A
PHASE_3B
PHASE_3C
PHASE_3D_STATIC
COMPLETE_VICIDIAL_SUITE
DEPLOYMENT_PARITY
SYSTEMD_STATIC_VALIDATION
PRODUCTION_COMPOSE_VALIDATION
SHADOW_COMPOSE_VALIDATION
INSTALLED_MANIFEST
INSTALLED_MANIFEST_TAMPER_TESTS
RELEASE_MANIFEST
RELEASE_TUPLE
OCI_LAYOUT
OCI_INDEX_SCHEMA_VERSION
OCI_BLOB_DIGESTS
OCI_DESCRIPTOR_SIZES
OCI_LAYER_DIFF_IDS
OCI_TAMPER_TESTS
EXACT_IMAGE_RUNTIME
CONTAINER_RESTART
SBOM
PROVENANCE
TRIVY
GRYPE
VULNERABILITY_ADJUDICATION
IMAGE_SECRET_SCAN
EVIDENCE_SECRET_SCAN
```

Artifact upload must remain fail-closed: no host release, OCI candidate, SBOM, provenance, manifest, scan evidence, or checksum artifact may upload after a failed identity, secret, vulnerability, runtime, manifest, or evidence gate.

All checksum sidecars must use portable download-relative filenames.

## Commit and push governance

Use normal focused commits only.

```text
REBASE=PROHIBITED
AMEND_PUBLISHED_COMMITS=PROHIBITED
FORCE_PUSH=PROHIBITED
NORMAL_PUSH_ONLY=REQUIRED
```

The implementation branch is:

```text
fix/vicidial-r3-dedicated-consolidation-20260827
```

Do not merge the PR as part of implementation. Do not self-approve.

## Safety boundary

Do not:

- deploy or publish a production image;
- modify `/current` or `/opt/codestra`;
- restart or reload services;
- access or mutate live MariaDB;
- replay, delete, or drain queues;
- change host or Hetzner firewall rules;
- modify ports `9443` or `9444`;
- enable dialing, callbacks, recording, lead publication, WebSocket production traffic, SMS, email, or external delivery.

The restricted-adapter network contract remains:

```text
10.40.0.1
  -> 10.40.0.2:8443
  -> 127.0.0.1:8097
```

## Completion report

Return:

```text
TARGET_REPOSITORY=appolon1908-hue/Vicidialer-Codestra
START_SHA=1c84e8bec7e56288adb87c96f292362d33547cff
SOURCE_BASE=ccb4ec0d5218501d724035da6be3540319c7513b
SOURCE_HEAD=bf4b04c8f0e0bcceb54edad33aaf51dfd5379a4f
FINAL_HEAD=<sha>
NEW_COMMITS=<count>
SOURCE_FILES_RECONCILED=<count>
DEDICATED_FIXES_PRESERVED=<PASS|FAIL>
P2_FINDINGS_FIXED=<count>
COMPLETE_TEST_RESULT=<result>
EXACT_IMAGE_EVIDENCE=<result>
WORKTREE=<CLEAN|DIRTY>
NORMAL_PUSH=<PASS|FAIL>
BRANCH_CLEANUP_REPORT=<path>
PR_DRAFT=YES
MERGE=HOLD
DEPLOYMENT=HOLD
LIVE_CAPABILITIES=DISABLED
```
