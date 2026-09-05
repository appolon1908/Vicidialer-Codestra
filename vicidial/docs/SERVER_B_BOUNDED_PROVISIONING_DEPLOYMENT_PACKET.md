# Server B bounded provisioning deployment packet

Status: `PREPARED_NOT_APPLIED`

This packet is for an existing authorized Server B administrator. It does not
authorize account creation, login activation, dialing, or live call control.

## Protected release inputs

Before application, replace these fields from the successful protected `main`
release manifest and verify them independently:

```text
SOURCE_SHA=<exact protected main commit>
ARTIFACT_REFERENCE=ghcr.io/appolon1908-hue/vicidialer-codestra@sha256:<digest>
SIGNATURE_VERIFICATION=PASS
SBOM_VERIFICATION=PASS
PROVENANCE_VERIFICATION=PASS
HIGH_CRITICAL_SCAN=PASS
```

Do not deploy a branch tag, an earlier PR image, or an artifact whose digest is
not bound to `SOURCE_SHA` by the signed provenance.

Export the complete `ARTIFACT_REFERENCE` as `VICIDIAL_ADAPTER_IMAGE`. Validate
the protected release manifest and Compose contract before rendering:

```text
python vicidial/scripts/validate_deployment_artifact.py \
  --release-manifest <downloaded-release-manifest.json> \
  --compose vicidial/docker/compose.production.yml
VICIDIAL_ADAPTER_IMAGE=<the validator's complete ghcr.io/...@sha256 output> \
  docker compose -f vicidial/docker/compose.production.yml config --quiet
```

## Preserved rollback evidence

The existing operator evidence is authoritative and must not be regenerated
unless its freshness policy expires or the production state materially changes:

```text
BACKUP_REFERENCE=20260905T103726Z
BACKUP_LOG=/var/log/codestra-vicidial-operator/20260905T103726Z-backup-19740.log
BACKUP_LOG_SHA256=3b6dc251c49adb57d0b43e3ae330f797a8c677bd17d943fb9faf0cdaf86dc886
VALIDATION_LOG=/var/log/codestra-vicidial-operator/20260905T103850Z-validate-backup-20087.log
VALIDATION_LOG_SHA256=75162a2c48db2766cd9af7fa82a1995b1d9a09415a5abd0c68f652354196cb45
RESTORE_LOG=/var/log/codestra-vicidial-operator/20260905T103912Z-restore-test-20249.log
RESTORE_LOG_SHA256=7ca2d5af0970d62382b83d4397d2cd7d7e3ae0d1114f15ec6c1fc30e67b9bcfa
ROLLBACK_LOG=/var/log/codestra-vicidial-operator/20260905T104009Z-rollback-plan-21151.log
ROLLBACK_LOG_SHA256=d73c4cf86190fcb1f90bd16e8bc95c344276c9a2e04e2f4945fc252a996c0ca3
ENCRYPTED_VALIDATION=PASS
OFF_SERVER_COPY=CONFIRMED
ISOLATED_RESTORE=PASS
RESTORE_PUBLIC_PORTS=0
RESTORE_RESIDUAL_RESOURCES=0
ROLLBACK_READY=PASS
```

## Per-check evidence register

Every administrative check must populate every column. `N/A` is allowed only
for artifact reference when a check predates the release; no field may be
omitted and every evidence object is recorded with its full SHA-256.

| Check name | Command or CI run | Environment/host | Source revision | Artifact reference | UTC timestamp | Result | Evidence location | Evidence SHA-256 |
|---|---|---|---|---|---|---|---|---|
| Encrypted backup | restricted operator backup (log records exact command) | production / `static` (`65.21.67.207`) | N/A | N/A | `2026-09-05T10:37:26Z` | PASS | `/var/log/codestra-vicidial-operator/20260905T103726Z-backup-19740.log` | `3b6dc251c49adb57d0b43e3ae330f797a8c677bd17d943fb9faf0cdaf86dc886` |
| Backup validation and off-host copy | restricted operator validation (log records exact command) | production / `static` (`65.21.67.207`) | N/A | N/A | `2026-09-05T10:38:50Z` | PASS | `/var/log/codestra-vicidial-operator/20260905T103850Z-validate-backup-20087.log` | `75162a2c48db2766cd9af7fa82a1995b1d9a09415a5abd0c68f652354196cb45` |
| Isolated restore rehearsal | restricted operator restore test (log records exact command) | isolated restore / `static` (`65.21.67.207`) | N/A | N/A | `2026-09-05T10:39:12Z` | PASS | `/var/log/codestra-vicidial-operator/20260905T103912Z-restore-test-20249.log` | `7ca2d5af0970d62382b83d4397d2cd7d7e3ae0d1114f15ec6c1fc30e67b9bcfa` |
| Rollback applicability | restricted operator rollback plan (log records exact command) | production / `static` (`65.21.67.207`) | N/A | N/A | `2026-09-05T10:40:09Z` | PASS | `/var/log/codestra-vicidial-operator/20260905T104009Z-rollback-plan-21151.log` | `d73c4cf86190fcb1f90bd16e8bc95c344276c9a2e04e2f4945fc252a996c0ca3` |
| Protected release identity | `<protected workflow run URL/ID>` | GitHub Actions protected branch | `<40-char source SHA>` | `ghcr.io/appolon1908-hue/vicidialer-codestra@sha256:<64 hex>` | `<UTC>` | `<PASS/FAIL>` | `<immutable evidence URL/path>` | `<64 hex>` |
| Production Compose render | `docker compose ... config --quiet` | administrative staging / `static` | `<40-char source SHA>` | `<complete immutable artifact reference>` | `<UTC>` | `<PASS/FAIL>` | `<sanitized evidence path>` | `<64 hex>` |
| Runtime policy offline validation | `agent_provisioning_policy.py validate ...` | administrative staging / `static` | `<40-char source SHA>` | `<complete immutable artifact reference>` | `<UTC>` | `<PASS/FAIL>` | `<sanitized evidence path>` | `<64 hex>` |
| Default disabled readback | `/health`, `/ready`, `/version` plus sanitized configuration readback | production / `static` | `<40-char source SHA>` | `<complete immutable artifact reference>` | `<UTC>` | `<PASS/FAIL>` | `<sanitized evidence path>` | `<64 hex>` |

The four preserved rows record completed evidence. Template rows are
deliberately unfilled and are not PASS until the authorized administrative
step creates and hashes their evidence.

## Administrative sequence

1. Verify the exact OCI digest, keyless signature, SBOM attestation, provenance
   attestation, source revision, and release metadata before pulling the image.
2. Install the image with `vicidial/docker/compose.production.yml`. Confirm every
   business-write, dialing, delivery, and live-call-control flag is `false` and
   that `/health`, `/ready`, and `/version` return the expected source revision.
3. Confirm MariaDB remains local-only. Apply
   `vicidial/ops/agent-provisioning-db-grants.sql.template` using the existing
   database administrative channel. It grants only the named insert columns and
   `UPDATE(active)` on `asterisk.vicidial_users` to the local adapter identity.
   Keep `agent-provisioning-db-grants-revoke.sql.template` staged as the matching
   emergency and completion revoke.
4. Create the protected exact-user policy outside source control with the
   canonical `vicidial/scripts/agent_provisioning_policy.py generate` command
   and validate its structure against
   `vicidial/config/agent-provisioning-policy.schema.json`. Supply
   `--agent-json`, `--release-sha`, `--authorization-reference`, `--expires-at`,
   `--backup-reference`, `--backup-created-at`, `--runtime-gid 10001`, the readable host checksum
   source in `--backup-artifact`, and its container-visible path under
   `/run/provisioning-backup/` in `--runtime-backup-artifact`. Supply the
   protected destination as `--output`; the generator creates a new root-owned,
   runtime-group-owned `0640` file and refuses to overwrite one. Run it through
   the existing authorized root administrative session so it can assign GID 10001.
   Revalidate
   with `agent_provisioning_policy.py validate --policy <path> --release-sha
   <sha> --backup-artifact-source <host backup path>`. Never place policy
   contents in evidence.
5. Mount only the protected policy file and validated backup directory read-only
   with `compose.agent-provisioning.yml`. This profile may enable only
   `ENABLE_VICIDIAL_WRITES` and `ENABLE_AGENT_PROVISIONING`; all other mutation
   and delivery flags remain false.
6. Use the canonical application client `POST /v1/agents/provision-disabled`
   with version-2 signing and `telephony:agent-provision`. Do not use the legacy
   provisioning endpoint or direct SQL for the account mutation.
7. Before any real request, prove rejection for a different user/campaign,
   changed role, absent/expired policy, invalid signature, replay, empty secret,
   and duplicate/conflicting identifier. Preserve only sanitized results.
8. After the one approved disabled-user request, read back the VICIdial user,
   level, group, campaigns, inbound groups, and `active=N`; reconcile the
   application mapping and audit correlation. On any mismatch, disable the user,
   run the reviewed compensation, revoke the temporary database grant, and keep
   history.
9. Revoke the bounded database grant after the single-user operation and verify
   a subsequent provisioning attempt is denied. Keep dialing, login activation,
   email/SMS, and live-call-control gates closed.

## Required sanitized return

```text
HOSTNAME=static
SOURCE_SHA=
ARTIFACT_REFERENCE=
IMAGE_DIGEST_VERIFIED=
DEFAULT_WRITES_DISABLED=
BOUNDED_GRANT_INSTALLED=
POLICY_VALIDATED=
PROVISIONING_INTERFACE=/v1/agents/provision-disabled
NEGATIVE_TESTS=
ACTUAL_STATE_READBACK=
GRANT_REVOKED=
UNRELATED_GATES_CLOSED=
FINAL_STATUS=
```
