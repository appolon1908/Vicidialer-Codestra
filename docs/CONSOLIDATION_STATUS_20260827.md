# VICIdial R3 dedicated consolidation status — 2026-08-27

## Repository authority

```text
TARGET_REPOSITORY=appolon1908-hue/Vicidialer-Codestra
TARGET_BASE=main
TARGET_BASE_SHA=1c84e8bec7e56288adb87c96f292362d33547cff
IMPLEMENTATION_BRANCH=fix/vicidial-r3-dedicated-consolidation-20260827
IMPLEMENTATION_PR=2
IMPLEMENTATION_PRE_STATUS_SHA=b6cafdd12d3f053f1d1080725b40cc1180d39adb

SOURCE_REPOSITORY=appolon1908-hue/codestra-production-platform
SOURCE_MIGRATION_BASE=ccb4ec0d5218501d724035da6be3540319c7513b
SOURCE_RECONCILIATION_HEAD=bf4b04c8f0e0bcceb54edad33aaf51dfd5379a4f
SOURCE_PR=111
```

## Consolidated scope

The dedicated branch now contains the current VICIdial-only R3 work while
preserving the dedicated repository's post-migration fixes:

- deterministic installed-layout generation and tamper verification;
- complete release/build identity binding;
- two-build runnable and attested OCI architecture;
- OCI layout, index schema, descriptor, blob, layer and rootfs verification;
- mutation-time campaign adoption proof revalidation, including dialing levels;
- exact-image source, container, SBOM, provenance, vulnerability and secret gates;
- fail-closed artifact upload ordering;
- portable checksum sidecars;
- branch policy, source-reconciliation and non-destructive cleanup inventory.

Monorepo-only shared PostgreSQL files were deliberately excluded because they
are outside the dedicated VICIdial repository boundary.

## Branch governance

`main` is the only canonical long-lived branch. The current implementation
branch remains short-lived and its pull request remains a draft.

No historical branch has been deleted or rewritten. Cleanup is classification
only until a separate governance action proves reachability from `main`, no
open-PR dependency, and no retained tag or migration reference.

## Exact-head validation state

The implementation was locally validated by the controlled consolidation
workflow before its normal push. The resulting GitHub-generated commit caused
GitHub to create `action_required` pull-request runs with no jobs. This status
commit intentionally establishes a new owner-authored exact head so normal
source and container workflows can execute against the current tree.

The PR must remain draft until all exact-head workflows pass, Codex reviews the
unchanged head cleanly, and an independent non-owner reviewer approves the same
SHA.

## Network and live-system boundary

The restricted adapter remains on the approved path:

```text
10.40.0.1
  -> 10.40.0.2:8443
  -> 127.0.0.1:8097
```

Ports `9443` and `9444` are independently owned and are not modified by this
consolidation.

## Release state

```text
SOURCE_CONSOLIDATION=IMPLEMENTED
NORMAL_PUSH=PASS
EXACT_HEAD_CI=PENDING
CODEX_EXACT_HEAD_REVIEW=PENDING
INDEPENDENT_APPROVAL=PENDING
PROTECTED_MERGE=PENDING
FINAL_MERGED_SHA_IMAGE=MISSING
SIGNATURE=MISSING
REGISTRY_DIGEST=MISSING
DEPLOYMENT=HOLD
LIVE_CAPABILITIES=DISABLED
PRODUCTION_DIALING=DISABLED
RECORDING=DISABLED
CUSTOMER_TRAFFIC=NOT_AUTHORIZED
```

No repository result in this PR authorizes a live deployment. The final
production image must be rebuilt, signed, verified and published from the exact
protected merged `main` SHA, not from this feature branch.
