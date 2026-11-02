# Source-only image security policy refresh — 2026-11-02

This candidate requires independent exact-head review and normal protected merge. It does not certify a deployed runtime or authorize dialing, customer traffic, provider effects, deployment or production activation.

The complete scanners on source `355e87e51c95e95795c886a55170bd7979bbc8f6` found new Critical/High issues in Python 3.11.16 and zlib 1.3.2-r0. The expired September 26 policy could not legitimately waive them. The repair pins official Python 3.11.18 on the same Alpine 3.24 and Python 3.11 series, preserving all existing application code, security regressions, image identity, OCI, signature and scan checks.

Primary provenance:

- [Python 3.11.18 security release](https://www.python.org/downloads/release/python-31118/), October 25, 2026.
- [Official image catalog](https://github.com/docker-library/official-images/blob/master/library/python), source commit `83054588043b3a7ac1ce8324c9242164b632beb9`, directory `3.11/alpine3.24`.
- Official registry OCI index `sha256:09d17d5e4c16d0a7a1eea690bcb7c6ab8616b211f8421e429e97c1bef5bd0ed2`; the raw index SHA-256 was independently verified. Linux/amd64 manifest: `sha256:792eb538bc2bd4c13f05ace04d448c4c07e5e4f9babf23957ebf005d5a72d744`.

[Exact source `ae064c35d4455855c734657e02ac1e71ddd7803d` hosted image evidence](https://github.com/appolon1908/Vicidialer-Codestra/actions/runs/41491607125/job/125435766308) passed the complete source/container tests on Python 3.11.18, disposable database rollback, locked image build, nonroot and scoped socket checks, exact tested OCI binding, SBOM/provenance, full Trivy and Grype scans and image secret scan. Both complete vulnerability reports contained zero High/Critical findings; lower-severity findings remain visible under the original threshold. The unchanged verifier then rejected the expired policy.

Report SHA-256 values from the selected-field diagnostic at 17:31:58 UTC:

| Report | SHA-256 |
| --- | --- |
| GRYPE_REPORT.json | d52292d75e52b0a7815cefdba0fd037bc5657bd8bcd259ce2f2a40b436d8d315 |
| VULNERABILITY_REPORT.json | 52e8c52c6b9ddc163698c295412a03e46ad2c4594cf98b849688eddfc6ebc27b |
| PYTHON_SECURITY_REGRESSION.json | 33ab5f7137556fd48b9b0f094112485cee257a09ba883491636173a4a0c9c306 |
| IMAGE_METADATA.json | 1f4ca88bddd25cb673de7b47fc1b86c5a027376ac446edb51ed8a3f66dfc1e65 |

Actual runtime evidence: Python 3.11.18, Expat 2.9.0, OpenSSL/libcrypto3/libssl3 3.5.9, libuuid 2.42.3-r1; all three original bounded Python security regressions passed.

The new source policy removes every obsolete Python 3.11.16 adjudication entry. It accepts **no High/Critical exceptions**, including findings covered by those old regressions. The short new expiry is November 9, 2026. The unchanged verifier still rejects expired policies, wrong runtime versions, missing or mismatched scan/image identity, all Critical findings and every unadjudicated High finding. New tests exercise both scanner formats and prove the old exceptions cannot bypass the current policy; legacy verifier-engine cases remain explicit test-only fixtures. Fresh CI on the final candidate must rebuild and verify the exact candidate image again; these prior report hashes are review evidence, not an exemption or substitute for current-head validation.
