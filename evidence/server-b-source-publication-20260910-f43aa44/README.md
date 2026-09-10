# Verified Server B source publication

Protected source `f43aa44fa0082eb2bb15be9ec3950ddfd8bc72e1` includes merged PRs #40 and #41.

- Workflow: https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/34495432411 (attempt 1, success).
- Artifact: `10159591752`, expires `2026-10-10T15:25:28Z`.
- Archive SHA-256: `c4dda634e4601b812c8aa51fc36b7e73b641ce15fd8200610ad4146a5fcbf9a2`.
- ZIP digest was checked against the GitHub artifact API before extracting six ordinary allowlisted files.
- Cosign 3.1.2 verified the archive and provenance using the exact protected-main workflow identity and GitHub Actions OIDC issuer.
- The provenance subject, source SHA, builder SHA and run/attempt were checked against independently observed workflow metadata.
- The protected source verifier checked archive members, modes and checksums. A clean detached checkout rebuilt identical archive bytes.

Verification commands: `cosign verify-blob --bundle <bundle> --certificate-identity https://github.com/appolon1908-hue/Vicidialer-Codestra/.github/workflows/publish-server-b-source-release.yml@refs/heads/main --certificate-oidc-issuer https://token.actions.githubusercontent.com <archive-or-provenance>`; `python vicidial/scripts/verify_source_release.py --archive <archive> --source-sha f43aa44fa0082eb2bb15be9ec3950ddfd8bc72e1 --builder-sha f43aa44fa0082eb2bb15be9ec3950ddfd8bc72e1`; `python vicidial/scripts/build_source_release.py --source-root <protected-checkout>/vicidial --source-sha f43aa44fa0082eb2bb15be9ec3950ddfd8bc72e1 --builder-sha f43aa44fa0082eb2bb15be9ec3950ddfd8bc72e1 --output <rebuilt.tar.gz> --metadata <rebuilt-metadata.json>`. Verification ran on Core using Python 3.12.11; this did not change the production interpreter.

This evidence certifies the source publication only. It does not certify a deployed process, OCI image, database restore or authorized call. The full archive and original ZIP remain retained in the Core release audit directory. Production readiness remains subject to the database and policy checks in the release contract.
