# Issue #11 certification checkpoint — 2026-09-09

Status: **SERVER_B_NO_GO_WITH_EXACT_BLOCKERS**

Issue: https://github.com/appolon1908-hue/Vicidialer-Codestra/issues/11
Implementation: https://github.com/appolon1908-hue/Vicidialer-Codestra/pull/37
Coordination: platform issues #257 and #259.

## Implementation and isolated certification

Tested source commit: `6241d920ab82edf7a3dadf09585c14b182e858f2`.
Environment: isolated local Python 3.11.16 and Docker, linux/amd64; synthetic
identities only. This is not Server B runtime evidence.

This checkpoint adds schema-aware operational state readiness. An empty SQLite
file, missing replay table, incompatible mutation-epoch schema, different configured
state path, or missing file now makes readiness fail. The probe opens the file
read-only and does not create or repair state. It verifies required schema, not
complete database integrity, write capability, or backup restorability.

A legacy worker test previously installed a fake PyMySQL package process-wide.
That contaminated the combined suite and prevented real database tests from
connecting. Its optional dependency doubles now exist only while loading the
legacy worker fixture. The combined worker/database check passed 48 tests.

The preceding PR #37 implementation provides campaign authority checks, durable
versions and replay results, atomic completion/audit, consistent versioned
read-back and callback recovery. It also fixes the reviewed agent state/version
race through version, pending-claim and mutation-epoch observations.

## Local build identity

Container source label: `6241d920ab82edf7a3dadf09585c14b182e858f2`.
Local image ID: `sha256:bc97ae9cb36cfc22de90d263be0d9c33c5df46f3b434bc15005b45b2ce6e81ae`.
Build date argument: `2026-09-09T16:10:46Z`.
Build used the checked-in Dockerfile and pinned base image/dependencies.
This local image is not a protected-main signed registry release.

MariaDB fixture image:
`mariadb:11.4@sha256:611a2fcc5fa7c6ceb8644c6f74b25ede004ff6c3a6b38c8f8c23d3bbf6c26430`.
The disposable fixture exposed only a loopback ephemeral port. It contains no
customer data. Database tests prove transactional user/phone rollback on injected
failures and reject nontransactional MyISAM tables before mutation. These tests do
not establish live MyISAM backup/restore or production schema compatibility.

The container smoke test runs with no network, a read-only root filesystem,
non-root UID, dropped capabilities and synthetic HMAC material. It checks health,
readiness, source identity, disabled dialing and restart. Its repository is the
candidate memory backend, not the production VICIdial database.

## Final verification

2026-09-09 16:12 UTC, tested source and image identities above:

```text
PYTHONPATH=vicidial/src python -m pytest -q vicidial/tests tests
619 passed, 0 skipped, 75 subtests passed, 1 upstream deprecation warning
```

The Python 3.11.16 test environment was on PATH. Both integrations were enabled:
`VICIDIAL_TEST_MYSQL_PORT=32770` with the synthetic CI password,
`VICIDIAL_CONTAINER_ENGINE=docker`, and
`VICIDIAL_CONTAINER_TEST_IMAGE=vicidial-issue11:6241d92`.
The full passing output is [checked in](issue11-certification-20260909-tests.txt).
Ruff on changed source/tests, source compilation and Git whitespace checks passed.
Build log: `/tmp/vicidial-issue11-final-build.log`; complete local test log:
`/tmp/vicidial-issue11-certified-tests.log`.

## Exact remaining closure gates

| Gate | Current evidence / blocker |
| --- | --- |
| Protected merge and release | PR #37 remains open; no protected merge or signed immutable release for these changes |
| Server B inventory and source match | SentinelX returned zero connected hosts; no approved Server B SSH entry configured |
| Private mTLS, source firewall, local-only DB/AMI and grants | No current live observation available |
| Synthetic provisioning and isolation on deployed VICIdial | Local regression and isolated MariaDB proof only; deployed staging not run |
| Asterisk/PJSIP endpoint registration and trunk health | Not observed live |
| Signed callback delivery through Middleware | Source regression coverage; live Server A/B delivery not certified |
| Backup, off-host copy, restore and rollback | No current host-bound backup/restore/rollback evidence |
| Approved call and terminal read-back | No approved destination/window/canary record supplied; no live CDR/disposition/positive duration/hangup proof |

No deployment, production configuration change, live provisioning, or call was
performed. Calls placed by this task: **0**. Production dialing and telephony
administration were not enabled. Keep issue #11 open until the live gates above
are evidenced against the exact protected release. Do not substitute local test
success for runtime certification.
