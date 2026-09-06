# Verified Server B source publication

This record covers source publication only. No live deployment, current-symlink
change, activation, enrollment, SIP registration or Odoo call occurred.

PR #30 received independent approval from kazan555 on exact head
`b1654fc3a62ba2ef963135d989119161b06c5b74`; required checks passed and there were
no unresolved review threads. Normal protected squash merge produced
`def0f1822fe54ab2a0800e8d6e083533df88d064` at 2026-09-06T00:09:41Z.
PR #28 remains merged; its completed repairs were preserved.

The new main-only [publication run 34000565825, attempt 1](https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/34000565825)
ran the corrected editable dependency preparation. Job `101398562531` and every
step passed, including 11 exact-source archive tests, actual extracted-package
installation, archive verification, both signatures and signed provenance
semantics. It completed 2026-09-06T00:10:27Z. The older failed run 33999730332/1
remains historical failure evidence.

## Verified downloaded tuple

| Field | Observed value |
|---|---|
| Protected builder/workflow SHA | `def0f1822fe54ab2a0800e8d6e083533df88d064` |
| Runtime source SHA | `9ac8ef4840f78ba4ad9b816e4e409298505103ce` |
| Publication run/attempt | `34000565825/1` |
| GitHub artifact ID | `9979357196` |
| Artifact name | `server-b-source-release-9ac8ef4840f78ba4ad9b816e4e409298505103ce` |
| Archive | `codestra-vicidial-adapter-9ac8ef4840f78ba4ad9b816e4e409298505103ce.tar.gz` |
| Archive SHA-256 | `5e3d4083dd94297c1085a28953547d5532e07c9685560184047208ca2cf0a252` |
| Artifact ZIP SHA-256 | `15fe0e95c664ddea96c86b181136b12a30e01d7d96ddc2d7d25cddb57c783f49` |
| Embedded manifest SHA-256 | `84275a426d21edecb121334a58af4f69219a4320d071b2352a996a8f8f739bdb` |
| Checksummed payload file count | `109` |

The ZIP digest was matched against GitHub's artifact API before its six expected
regular members were copied into a user-owned verification directory. The actual
downloaded archive digest matches its adjacent checksum and metadata. The
protected builder's independent archive verifier passed with the independently
expected source and builder SHAs: exact member/checksum set, canonical paths,
regular member types, safe modes, sensitive-file rejection, embedded SOURCE_SHA
and manifest count/digests all passed. The metadata sidecar was matched to the
manifest inside the signed archive, not treated as an independent authority.

Cosign v3.1.2 binary checksum was verified against the protected workflow's
`f7622ed3cf22e55e1ae6377c080979ff77a22da9981c11df222a2e444991e7cf`.
Local `verify-blob` returned `Verified OK` independently for the archive bundle and
provenance bundle, using the exact certificate identity
`https://github.com/appolon1908-hue/Vicidialer-Codestra/.github/workflows/publish-server-b-source-release.yml@refs/heads/main`
and issuer `https://token.actions.githubusercontent.com`.

After signature verification, the protected workflow's actual Python semantic
checks were rerun against the downloaded signed statement, with expected values
from the independently observed protected run. Statement type, predicate type,
archive name/digest, repository/source SHA, builder SHA, workflow identity and
run/attempt all matched. Initially that local invocation failed because the
workflow expects `dist/source-release-provenance.json`; recreating that layout
with the identical verified bytes allowed the unchanged checks to pass. No
validation condition was removed or converted from failure to success.

Local builds with this exact source and protected builder reproduced the actual
published archive bytes under umasks 0022, 0027 and 0077. Strict rejection of
untracked payloads remains intact. Detailed timestamped results and artifact
expiry are in [verification.json](verification.json).

## Runtime boundary

This signed source archive is compatible with the documented source-package
installer shape; it is not an OCI image and is not proof of production
installation. The prior process/current mismatch, reviewed administrator
transaction/backup/rollback requirements, exact 6901 identity/secret-authority
contract, registration/route-isolation evidence and human browser acceptance
remain separate prerequisites. No runtime inspection or mutation was needed for
this publication continuation. cod00016/6101 and all production gates were left
unchanged. No calls were made and no one-call grant was issued or replenished.

## Auditable rerun (review 3942479222)

The earlier aggregate observation is preserved. A fresh rerun, recorded in
[audit/audit.json](audit/audit.json), provides exact argv/cwd/umask, start and end
UTC times, exit codes, environment, verifier SHA-256, and retained output paths
and SHA-256 for each check. Its timestamps are new observations, not reconstructed
timestamps for the original verification. The executable is
[audit_verification.py](audit_verification.py); its complete invocation is in the
record. It performs no deployment or telephony action. Required inputs are the
downloaded artifact directory, detached source and builder checkouts, and the
checksum-verified Cosign binary. Python assertions must be enabled (no `-O`).

Rerun interval: `2026-09-06T00:32:55.598963+00:00` through `2026-09-06T00:32:57.317261+00:00`.

| Original claim | Retained rerun checks |
|---|---|
| ZIP and outer checksum; manifest/sidecar binding | [zip-archive-manifest.txt](audit/zip-archive-manifest.txt) |
| Member checksums, safe members/modes, embedded source/builder | [archive-members.txt](audit/archive-members.txt), [source-identity.txt](audit/source-identity.txt), [builder-identity.txt](audit/builder-identity.txt) |
| Archive signature | [archive-signature.txt](audit/archive-signature.txt) |
| Provenance signature | [provenance-signature.txt](audit/provenance-signature.txt) |
| Signed source/builder/digest/workflow/run/attempt | [provenance-bindings.txt](audit/provenance-bindings.txt) |
| Deterministic published bytes | `rebuild-0022/0027/0077` and `rebuild-digest-0022/0027/0077` entries and outputs in the audit record |
| Tool identity | [tool-git.txt](audit/tool-git.txt), [tool-cosign-version.txt](audit/tool-cosign-version.txt), [tool-cosign-checksum.txt](audit/tool-cosign-checksum.txt), Python/platform/UID in audit record |

The [public-signatures directory](public-signatures/SHA256SUMS) retains the exact
public Sigstore bundles, signed provenance and matching metadata, with checksums.
It contains no private key, enrollment token, application credential or archive
payload. To repeat archive cryptographic verification after artifact expiry,
rebuild the source archive from the pinned source and builder using the recorded
commands and require the published SHA-256, then use the retained public bundles
with the recorded `cosign verify-blob` commands. The original GitHub transport ZIP
checksum remains a historical download observation; reconstructing the tar does
not claim to reconstruct GitHub's ZIP. The full audit's ZIP check still requires
the locally retained original ZIP. Tool trust data/network availability and the
pinned Git objects are prerequisites for a future cryptographic rerun.
