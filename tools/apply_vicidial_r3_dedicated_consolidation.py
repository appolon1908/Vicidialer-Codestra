#!/usr/bin/env python3.11
"""Apply the reviewed VICIdial-only consolidation delta exactly once."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: Path, old: str, new: str, expected: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != expected:
        raise RuntimeError(
            f"anchor count mismatch for {path}: expected {expected}, found {actual}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


repository = ROOT / "vicidial/src/codestra_vicidial/repository.py"

replace_exact(
    repository,
    '            "campaign_recording,scheduled_callbacks,campaign_description "\n'
    '            "FROM vicidial_campaigns WHERE campaign_id=%s", (actual_id,))',
    '            "campaign_recording,scheduled_callbacks,campaign_description,"\n'
    '            "hopper_level,auto_dial_level "\n'
    '            "FROM vicidial_campaigns WHERE campaign_id=%s", (actual_id,))',
)

replace_exact(
    repository,
    '            "campaign_recording,scheduled_callbacks,campaign_description "\n'
    '            "FROM vicidial_campaigns WHERE campaign_id=%s", (spec.actual_id,))',
    '            "campaign_recording,scheduled_callbacks,campaign_description,"\n'
    '            "hopper_level,auto_dial_level "\n'
    '            "FROM vicidial_campaigns WHERE campaign_id=%s", (spec.actual_id,))',
    expected=2,
)

replace_exact(
    repository,
    '            "scheduled_callbacks": "Y" if spec.callback_enabled else "N",\n'
    '            "campaign_description": description,\n',
    '            "scheduled_callbacks": "Y" if spec.callback_enabled else "N",\n'
    '            "campaign_description": description,\n'
    '            "hopper_level": 0, "auto_dial_level": "0",\n',
    expected=2,
)

replace_exact(
    repository,
    '            "campaign_recording", "scheduled_callbacks", "campaign_description",\n'
    '        )',
    '            "campaign_recording", "scheduled_callbacks", "campaign_description",\n'
    '            "hopper_level", "auto_dial_level",\n'
    '        )',
)

replace_exact(
    repository,
    '''        if legacy_update:
            self._execute(
                "UPDATE vicidial_campaigns SET campaign_name=%s,active='N',dial_method=%s,"
                "local_call_time=%s,campaign_recording=%s,scheduled_callbacks=%s,"
                "campaign_description=%s,hopper_level=0,auto_dial_level='0' WHERE campaign_id=%s",
                (spec.name, spec.dial_method, spec.call_time, spec.recording_policy,
                 desired_campaign["scheduled_callbacks"], description, spec.actual_id),
            )
''',
    '''        if legacy_update:
            affected = self._execute(
                "UPDATE vicidial_campaigns SET campaign_name=%s,active='N',dial_method=%s,"
                "local_call_time=%s,campaign_recording=%s,scheduled_callbacks=%s,"
                "campaign_description=%s,hopper_level=0,auto_dial_level='0' "
                "WHERE campaign_id=%s AND campaign_name<=>%s AND active='N' "
                "AND dial_method<=>%s AND local_call_time<=>%s "
                "AND campaign_recording<=>%s AND scheduled_callbacks<=>%s "
                "AND campaign_description<=>%s "
                "AND hopper_level<=>%s AND auto_dial_level<=>%s",
                (spec.name, spec.dial_method, spec.call_time, spec.recording_policy,
                 desired_campaign["scheduled_callbacks"], description, spec.actual_id,
                 current.get("campaign_name"), current.get("dial_method"),
                 current.get("local_call_time"), current.get("campaign_recording"),
                 current.get("scheduled_callbacks"),
                 current.get("campaign_description"), current.get("hopper_level"),
                 current.get("auto_dial_level")),
            )
            if affected != 1:
                raise CollisionError(
                    f"campaign adoption proof became stale: {spec.actual_id}"
                )
''',
)

write(
    ROOT / "docs/BRANCH_POLICY.md",
    """# Branch policy

## Canonical branch

`main` is the only long-lived canonical branch. It must represent reviewed,
merged source—not an environment deployment pointer.

## Change flow

```text
main
  <- short-lived feature/fix/chore branch
  <- draft pull request
  <- exact-head source and container CI
  <- Codex review on the unchanged head
  <- independent non-owner approval on the unchanged head
  <- protected merge
  <- final artifact build from the protected merged SHA
```

## Required controls

- Never push implementation work directly to `main`.
- Never rebase, amend, or force-push after review begins.
- Any new commit invalidates prior exact-head CI and approval.
- Production identity is the protected merged Git SHA plus the signed immutable
  registry digest; branch names are not deployment authority.
- Build, SBOM, provenance, vulnerability, manifest, OCI, signature and release
  tuple evidence must all name the same exact SHA and image digest.
- Release artifacts upload only after all security and identity gates succeed.
- All checksum sidecars use download-relative portable filenames.

## Branch retention

Imported migration branches are historical lineage until a separate retention
change proves they are reachable from `main`, are not referenced by a tag or
migration map, and have no open pull request. Branch deletion is never bundled
with implementation work.

## Safety

Repository work does not authorize deployment. Dialing, callbacks, recording,
lead publication, WebSocket production traffic, SMS, email and external
delivery remain disabled until separately certified and approved.
""",
)

write(
    ROOT / "docs/BRANCH_CLEANUP_20260827.md",
    """# Branch cleanup inventory — 2026-08-27

This inventory is non-destructive. No branch was deleted, rewritten, rebased or
force-updated.

| Branch | Classification | Disposition |
|---|---|---|
| `main` | Canonical | Protect and accept changes only through reviewed PRs. |
| `fix/vicidial-r3-dedicated-consolidation-20260827` | Active | Current short-lived consolidation PR; delete only after protected merge and post-merge validation. |
| `chore/verify-vicidial-import` | Merged deletion candidate | PR #1 was merged; verify tag/migration references once more before deletion in a separate governance change. |
| `agent/fix-vicidial-operator-probe-20260821` | Historical lineage retained | Imported source lineage; not deployment authority. |
| `agent/vicidial-canonical-issuer-fix-20260822` | Historical lineage retained | Imported source lineage; not deployment authority. |
| `agent/vicidial-reconciliation-db-grants-20260822` | Historical lineage retained | Imported source lineage; not deployment authority. |
| `baseline/vicidial-adapter-local` | Historical lineage retained | Baseline mapping retained for audit and comparison. |
| `feature/vicidial-asterisk-core` | Historical lineage retained | Feature lineage retained; not canonical. |
| `fix/vicidial-post-merge-review` | Historical lineage retained | Post-merge migration evidence retained. |
| `fix/vicidial-r3-phase3-remediation` | Historical lineage retained | Monorepo Phase 3 lineage retained; superseded by the dedicated consolidation PR. |
| `release/production-activation` | Historical lineage retained | Mutable release branch is not dedicated-repository deployment authority. |
| `release/vicidial-adapter` | Historical lineage retained | Legacy source lineage only. |
| `release/vicidial-adapter-r3-production-hardening` | Historical lineage retained | R3 source lineage only; final release must be built from protected `main`. |

## Cleanup gate

A branch may be removed only when all are true:

1. its tip is reachable from protected `main`;
2. no open PR uses it as head or base;
3. no retained tag, release, migration manifest or audit bundle references it;
4. exact-head CI evidence is already preserved elsewhere;
5. deletion is approved as a separate governance action.

Until those checks are automated, historical branches remain retained rather
than being guessed safe to delete.
""",
)

write(
    ROOT / "docs/SOURCE_RECONCILIATION_20260827.md",
    """# VICIdial source reconciliation — 2026-08-27

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
""",
)

for relative in (
    ".github/workflows/apply-vicidial-r3-dedicated-consolidation.yml",
    "tools/apply_vicidial_r3_dedicated_consolidation.py",
):
    (ROOT / relative).unlink()

print("DEDICATED_CONSOLIDATION_PATCH=PASS")
