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
4. Create the protected exact-user policy outside source control. It must match
   the schema in `BOUNDED_AGENT_PROVISIONING.md`, bind the exact release SHA and
   approved request, reference the mounted encrypted backup by absolute path and
   checksum, and have an explicit expiry. Never place its contents in evidence.
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
