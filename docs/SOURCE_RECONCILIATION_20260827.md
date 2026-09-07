# VICIdial source reconciliation — 2026-08-27

## Authority

```text
TARGET_REPOSITORY=appolon1908-hue/Vicidialer-Codestra
TARGET_START=1c84e8bec7e56288adb87c96f292362d33547cff
SOURCE_REPOSITORY=appolon1908-hue/codestra-production-platform
SOURCE_MIGRATION_BASE=ccb4ec0d5218501d724035da6be3540319c7513b
SOURCE_HEAD=bf4b04c8f0e0bcceb54edad33aaf51dfd5379a4f
SOURCE_PR=111
```

## Reconciled VICIdial scope

- immutable installed-layout generation and verification;
- exact Docker/OCI identity verification;
- OCI layout, index, descriptor, blob, layer and rootfs authentication;
- fail-closed artifact upload and portable checksum sidecars;
- campaign adoption mutation-time proof revalidation;
- exact-image source, container, SBOM, provenance, vulnerability and secret
  evidence;
- dedicated source and container validation workflows.

## Dedicated changes preserved

- two-build runnable/attested container architecture;
- active workflows under `.github/workflows/`;
- historical workflow copies under `ci/workflow-history/`;
- installed-manifest stability exclusions for runtime bytecode/cache files;
- dedicated migration mapping and documentation.

## Explicitly excluded

The following monorepo-only shared PostgreSQL changes were not imported:

```text
operations/shared-postgresql/PITR_RUNBOOK.md
operations/shared-postgresql/install-backup-hba.sh
tests/test_shared_postgresql_pitr.py
```

They belong to the production-platform repository, not the dedicated VICIdial
repository.

## Network boundary

The restricted adapter remains:

```text
10.40.0.1 -> 10.40.0.2:8443 -> 127.0.0.1:8097
```

Ports `9443` and `9444` remain independently owned and were not modified.

## Release boundary

This reconciliation does not deploy or authorize customer traffic. A final
production image must be rebuilt, signed and verified from the protected merged
`main` SHA—not from this feature branch.
