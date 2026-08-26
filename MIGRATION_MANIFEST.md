# Migration manifest

- Source repository: `appolon1908-hue/codestra-production-platform`
- Destination repository: `appolon1908-hue/Vicidialer-Codestra`
- Extraction generated: `2026-08-26T20:08:14Z`
- Main source branch: `fix/vicidial-r3-phase3-remediation`

The extraction retains paths whose names contain `vicidial`, the complete
`campaign_provisioning/` gateway, and explicitly related integration,
security, test, evidence, and repository-governance files. Historical GitHub
workflow files are retained under `ci/workflow-history/`. Commit topology,
authors, messages, branch names, and VICIdial tags are retained; commit SHAs
are necessarily rewritten because unrelated monorepo paths were removed.

| Branch | Source monorepo SHA | Filtered destination SHA |
|---|---|---|
| `agent/fix-vicidial-operator-probe-20260821` | `3ca6621a807e1bc30f01d6d45430540bc0f24d2c` | `4fd84b24bccc37309e280302f2ec0d0f4a26c230` |
| `agent/vicidial-canonical-issuer-fix-20260822` | `ae34a2ff37f0758cf4a3f9c6a64c5ebae6778982` | `6377cdd244743e3bef345aebff305d4138951f04` |
| `agent/vicidial-reconciliation-db-grants-20260822` | `bbae678daf6cf2d0eadfd35fcc4de1eaf5008f08` | `751e1f13cda38a5362cf0742f1aed9d91307f2e4` |
| `baseline/vicidial-adapter-local` | `e8696204ce3a28e1d452d573b4ccc528069dfd91` | `ba0c74543f45c758efbf93145d621b46784bf4c1` |
| `feature/vicidial-asterisk-core` | `92fac3351cbb41c9c1c144c3d1039644be595dae` | `c4c70961e94c0f2a670738a8b615d5d8e8bef888` |
| `fix/vicidial-post-merge-review` | `a15ec8fe8409a99bce75846b6f8f847efa0a268a` | `4a86a1e075a67b8891ef930556b34b36f8aca113` |
| `fix/vicidial-r3-phase3-remediation` | `ccb4ec0d5218501d724035da6be3540319c7513b` | `43f823079bee5e68be2e9194d6a6a17d893141f0` |
| `release/vicidial-adapter` | `f58ca120f6d7b858d37b6d76536bdb4a959648c9` | `ae7d4d6da3c3f64ed6c24ce5f65306a2ca27ffcd` |
| `release/vicidial-adapter-r3-production-hardening` | `93c44b5ec8189f0a9d8f021443b615c559cf113e` | `10a5836016ebdfdbb6bb02805e2a9b1b68956a98` |
| `release/production-activation` | `f3a16308194378a7b1580e04943dc98d64619077` | `5fc74fe80a5315880438785c9a85994fd615104e` |
