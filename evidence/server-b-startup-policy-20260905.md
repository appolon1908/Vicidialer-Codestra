# Server B continuation — 2026-09-05 22:57 UTC

PR #27 merged into #26; #26 subsequently merged into main as
75f48ffe9f4a48f76bc5ed0ecb3dc5770933d98a, final head
082bde27ee6eec556f503f30a2c08e306f5fe492. Earlier implementation snapshots are
historical and do not represent the final merged code.

Review comment 3942116173 identifies a remaining merged-source defect:
replacing the policy before the first originate can authorize a new identity,
while cleanup still binds to the immutable startup policy. This correction
compares the complete canonical policy digest before dispatch. Six regressions
cover tenant, subject, employee, authorization reference, route digest and expiry
replacement; they require no AMI action or consumed intent and verify that the
unchanged startup policy can still originate. Existing cleanup behavior remains.

Local isolated Python 3.12.14 validation:
`python -m pytest -q -rs vicidial/tests tests`: 421 passed, 6 skipped,
1 dependency deprecation warning. Five skips require isolated MariaDB and one
requires a built container. No production service was contacted by these tests.
Whitespace validation passed. Ruff reports the pre-existing TRY004 finding at
internal_calling.py's malformed-binding branch; this patch does not alter it.

Protected build 33993257058 succeeded for the old merged source, including
immutable-candidate. Its log records verification of:
`ghcr.io/appolon1908-hue/vicidialer-codestra@sha256:1f9c908ddef0be6f4c1f9b043eaa63cf9b101449aa8a13302e72f04a13ced6da`
using shared workflow 99972268792554e70f756134b544c5e49eee5398.
This is a workflow observation, not a new local signature verification or a
corrected artifact. Staging-readonly and production-readonly-canary were skipped.

The artifact predates this fix and must not be treated as its release proof.
PR #28 separately owns installer-compatible source-release packaging at observed
head 2ce749ccb48abe91f50e6a72f8960b6eff54c720; no parallel packaging change made.

Remaining gates: correction review/merge, corrected protected artifacts,
staging certification, actual runtime/manifest reconciliation, exact identity
policy, administrator deployment and human SIP/audio validation. No production
change, activation, enrollment, or call occurred in this continuation.
