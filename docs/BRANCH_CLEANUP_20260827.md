# Branch cleanup inventory — 2026-08-27

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
