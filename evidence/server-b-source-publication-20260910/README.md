# Verified source release and issue 11 integration evidence

On 2026-09-10, protected publication [run 34491694169/1](https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/34491694169)
passed for source and builder `8a3bb2f6977507fe850cd5d5ec4523f4b8880022`.
This includes merged PRs #36, #37, #38 and #39. It does not include the new
event gateway health correction in PR #40.

| Binding | Verified value |
| --- | --- |
| GitHub artifact | `10158038140` |
| Artifact expiry | `2026-10-10T14:51:18Z` |
| Archive SHA-256 | `4e05428e1e70e8d4703bcf15802778702069f9fb1f42d34a5cb52f8c93825ae7` |
| Download ZIP SHA-256 | `ecd9d380f9846a92d2a679a8e24dbb5671144fe292a353937e6f6cafe75f491d` |
| Embedded manifest SHA-256 | `798b6864c0c158460dad2881121af03abda93d3401cd208cd56bcea87af24cd3` |
| Payload files | 125 |

Independent download verification on core matched GitHub's ZIP digest before
extracting exactly the six expected regular files. The protected source verifier
validated members, modes, per-file checksums and source/builder bindings.
Cosign v3.1.2 was checksum-verified against the protected workflow, then verified
both archive and provenance signatures with the exact main workflow identity.
Signed provenance matched the observed run/attempt, repository, source/builder
SHAs and downloaded archive digest. Metadata matched the manifest inside the
signed archive. Rebuilding from the protected checkout reproduced the published
archive bytes. Retained logs and exact command records are in
[verification.json](verification.json).

The public signature bundles, signed provenance and metadata are retained here.
They contain no private key or runtime credential. Rebuild the archive with the
recorded protected source/builder command and require the archive hash above
before reusing the recorded `cosign verify-blob` commands. Rebuilding the tar does
not recreate GitHub's transport ZIP. The original download is retained in the
core release audit directory recorded in the command evidence.

## Current runtime findings

Read-only observations on Server B on 2026-09-10 at approximately 14:40–14:50 UTC:

- The restricted adapter process, working directory, release-local interpreter
  argv and `/version` identify `e6db3c49fb0dd4637cc8c1b9f21c5b22bb39c9f3`.
  `/health`, `/ready`, and `/version` return 200. The backend is memory and
  writes/provisioning/live control/dialing remain false. It predates the
  protected source release above.
- The passive gateway on port 9478 reports 87 pending events, an oldest age of
  about 26 days, and 51 quarantined events. The sync unit is inactive and
  `SEND_EVENTS=false`. No backlog was replayed or deleted.
- The integration monitor correctly reports the backlog. The Asterisk monitor
  correctly detects configuration-manifest drift in `pjsip.conf`,
  `pjsip-codestra-webrtc-test.conf`, `extensions-vicidial.conf`, and
  `http.conf`. No baseline was reset to hide that drift.
- The inspected `/usr/local/sbin/codestra-vicidial-operator` has SHA-256
  `015fda29ada5eb11d71a6177876fdf102c7fe83932dda91772e09994c67501ee`.
  Its `stage` action records backup readiness; its `deploy` action explicitly
  reports no mutation. Its generic restart action affects three units.
  Those actions do not implement the restricted adapter's documented
  release-local venv, verified artifact and single-unit rollback transaction.

Publication is complete for the identified protected source. Runtime rollout,
approved drift reconciliation, canonical event delivery and issue #11 production
certification remain separate incomplete steps. No live deployment, activation,
call, state deletion, or configuration-baseline replacement occurred here.
