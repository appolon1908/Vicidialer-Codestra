# Server B source correction and execution gates

Mission: `CODESTRA_ODOO_INTERNAL_CALL_END_TO_END_COMPLETION_20260905`, Server B lane.
Snapshot: 2026-09-05 23:40 UTC. Historical evidence files are preserved.

## Corrected source and candidate verification

- Protected runtime payload: `9ac8ef4840f78ba4ad9b816e4e409298505103ce`, merged PR #29.
- Packaging fixing commit: `7c7bbe8bd13dc31c7f413865545601f3a88c815b`, existing PR #28.
- Protected publication builder SHA/run/attempt: **not available** before protected merge/publication.
- Local unsigned archive: `codestra-vicidial-adapter-9ac8ef4840f78ba4ad9b816e4e409298505103ce.tar.gz`.
- Local candidate archive SHA-256: `57302153fcc01884e426a11f6dc34049e3c3efebc18d6e735bf2e0117cf32cfd`.
- Builds under umasks 0022/0027/0077 produced identical bytes and passed the independent archive verifier. This checksum binds the fixing commit as builder, not the future protected builder.
- Downloaded protected archive signatures and signed source/build/digest/run bindings: **NOT_RUN**, no protected source publication yet. An OCI candidate is not a substitute.

The fix updates source selection, confirmation, exact-source tests and current deployment documentation together. It adds installed-payload startup-policy substitution rejection and same-call cleanup regression execution. The signed provenance now also requires the independently expected builder SHA; source, archive digest, workflow and run/attempt bindings remain mandatory. Existing permission, sensitive-member, canonical-path, checksum and installed-package repairs are retained.

Validation of fixing commit:

- Local Python 3.12.14: `python -m pytest -q -rs vicidial/tests tests` with isolated `CODESTRA_ADAPTER_STATE` and basetemp: **446 passed, 6 skipped**. Five skips require isolated MariaDB; one requires a built test container. One dependency deprecation warning.
- Exact selected source, `SOURCE_RELEASE_TEST_ROOT` pointing to clean detached 9ac8ef source, archive/provenance suites: **25 passed**.
- [Exact-head source CI 33999065114](https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/33999065114): **PASS**, 446 passed / 6 skipped, plus 11 exact-selected archive/install tests; tree, history and diff secret scans passed.
- [Exact-head container CI 33999065166](https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/33999065166): **PASS**. Separately ran five isolated MariaDB rollback tests and 11 container contract tests; runtime-UID authority socket fixture, exact-image export, vulnerability adjudication and secret gate passed. Fixture success does not establish an installed production credential authority.
- Local Gitleaks tree/history/diff scans and Git whitespace check: **PASS**.
- Readiness source-CI/secret-scan and production completeness checks passed; release/staging/canary jobs were skipped, not certified.

## Direct host observations

At 23:39:49 UTC: hostname `static`, effective user `codestra-admin` UID/GID 1001, public address `65.21.67.207/26`, private address `10.40.0.2/24`. OS readback: openSUSE Leap 15.6.

The authorized sudo path allows only enumerated `/usr/local/sbin/codestra-vicidial-operator` lifecycle commands. It grants no general root shell or activation/enrollment/grant command. The installed executable is not readable by this user; local historical installers are not proof of the installed operator's current source or contract.

Current symlink and SOURCE.COMMIT: `a6bc9de3b010e3b2978425d3f787ce7f61826c67`. Authorized status reports the process interpreter under `b7ffe4d60a364f00411852aa327e2c424fb2f56a/.venv/bin/python3.11`, with service entrypoint `codestra_vicidial.app:app`. Loaded revision, immutable dependency identity and effective rollback predecessor therefore remain **UNRECONCILED**. No symlink, venv or service was changed.

- Status evidence: `/var/log/codestra-vicidial-operator/20260905T232739Z-status-16996.log`.
- Existing backup `20260905T103726Z` freshly validated **PASS**; no new backup started. Evidence: `/var/log/codestra-vicidial-operator/20260905T233029Z-validate-backup-18036.log`.
- Rollback-plan reports **PASS**; evidence `/var/log/codestra-vicidial-operator/20260905T233102Z-rollback-plan-18374.log`. This is a plan, not independently verified restored-process equivalence.
- Authorized runtime validator reports **PASS** at 23:37:44 UTC; evidence `/var/log/codestra-vicidial-operator/20260905T233744Z-validate-runtime-21037.log`. Health does not resolve the source mismatch.
- Isolated restore log contents/off-server restoration and current freshness coverage: not independently verified in this continuation. Do not restore over production.

## Coordination and precise outstanding prerequisites

[Middleware #145 update](https://github.com/appolon1908-hue/Middleware-/pull/145#issuecomment-5555556526) supplies the corrected source/candidate tuple and runtime gates. Refreshed Middleware head at this snapshot is `0c56a5ea261d9bca9bef0f565c5b35ddcdc8cd22`; its contract lock still records tested source `6f6a923eb117a22b8295ca72de14728a5458feae`. Server A owns testing and updating that pair. No concurrent Middleware edit was made.

1. **Independent reviewer:** ruleset 22293141 requires one approving review, approval of the latest push, stale-review dismissal, all review threads resolved, strict required source-CI/secret-scan checks and squash merge. Previous approvals do not cover this new head. After satisfaction, normal protected merge and main-only source publication with `PUBLISH_SERVER_B_SOURCE_RELEASE_9AC8EF48` are the prepared next actions; capture the resulting builder SHA and run/attempt and verify both downloaded signatures plus all bindings before staging.
2. **Server B operator administrator:** provide the installed operator source/checksum and reviewed deployment transaction that creates a fresh venv at its final release path, reconciles the effective predecessor, proves unchanged predecessor state in rehearsal, and reads back loaded source/imports/UID and rollback. The historical readable installer copies a venv; it is not approved deployment authority. No manual repoint or broad restart is prepared.
3. **Mission/secret-authority owner:** supply the named complete mission document and reviewed Appolon/6901 activation/deactivation/readback/enrollment/revocation implementation or its authoritative transport contract. The document was not found in accessible workspace or inspected repository trees. VICIdial's current `UnixCredentialAuthority` implements only bootstrap issuance; the provisioning service's browser flow defaults to staging endpoint 6101. No reviewed 6901 lifecycle implementation was located. An implementation successor needs the actual protected identity, secret-authority enrollment/revocation protocol and bounded operator contract; inventing those would create a competing authority. This missing code remains an explicit completion blocker, not a runtime feature claimed from provisioning-disabled.
4. **Server A owner and Appolon:** compatible installed pair, exact expiring one-call policy and secret authority, observed browser/Asterisk registration, verified TEST_ECHO/6000 isolation, approved Odoo URL/database/synthetic lead, then one browser-originated attempt with human audible-echo confirmation, same-call hangup, authoritative terminal evidence, one history row, no orphan legs and authority cleanup. These steps remain **NOT_RUN**.

No production configuration, account, grants, credentials, call state, symlink, venv or service was changed. Normal inspection audit logs were generated. cod00016/6101 was not changed. No call or redial occurred, and no one-call grant was issued or replenished. PSTN and unrelated delivery were not enabled. **MISSION_STATUS=BLOCKED**, not production-certified or complete.
