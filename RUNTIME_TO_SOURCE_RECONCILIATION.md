# Runtime-to-source reconciliation

Captured 2026-08-14 for `CHG-20260725-VICIDIAL-EVENT-PRODUCTION-CUTOVER-01`.

## Server A

Protected branch at preparation start: `release/production-activation` SHA `9bd1f69e59d061717de7ff8866220d68290ed004`.

| Runtime item | Runtime identity | Authoritative source state | Resolution |
|---|---|---|---|
| Event gateway/readiness | image `sha256:961c0569a5033cdb494d211fd32cfe20d7efd42d3900f02c2d3f0ac7bfdcfa63`, label `51416422eaaa959c8c7223ad1434287597eb8007` | Runtime is not protected-main release | Do not redeploy until exact protected artifact is built and signed |
| Readiness Compose overlay | SHA-256 `e3e4b132d038a1022bd71ddbc77f192e7838fa229022e1f0e1ab09bb768424e0` | Runtime-only overlay contains the corrected protected key mount and certificate pin | Convert to the protected deployment manifest before release |
| Private VICIdial ingress Caddyfile | SHA-256 `1bc06e3b8f4ada8f3116bd2e9d16af120785fd77db59d6e58a6eddb6fac088b3` | Runtime release-local correction removes destructive certificate-header transformations | Promote the reviewed ingress template; never trust caller identity headers |
| Redis ACL | SHA-256 `e4d0ec2d6f156d92382d73c83054b10bc66b0fbe8b5666779372906b1ac622cb` | Runtime contains bounded readiness nonce namespace | Represent only the ACL rule, never credentials, in configuration-as-code |
| Odoo database/filestore restore | paired backup `odoo-pair-20260814T150034Z`, off-server snapshot `98a338a0` | Validator, runbook and tests added by this PR | Passed isolated restore; zero missing/checksum mismatches |

Server A currently runs multiple middleware image identities. This is explained historical component-by-component deployment, not a single approved production tuple. Zero unexplained drift requires replacement by one reviewed deployment manifest whose component digests are explicit; it cannot be claimed before the signed release exists.

## Server B

Runtime restricted-adapter source/release SHA: `b7ffe4d60a364f00411852aa327e2c424fb2f56a` from `feature/voice-ai-telephony-integration`.

| Runtime item | Runtime identity | Authoritative source state | Resolution |
|---|---|---|---|
| Restricted adapter | release directory SHA `b7ffe4d…`; systemd symlink matches | Feature-branch artifact | Replace only with signed protected-main artifact after review |
| MariaDB adapter identity | table-scoped `SELECT`; DML denied | Desired least-privilege runtime already achieved | Add grant verification to deployment acceptance; do not grant writes |
| Observer server certificate | candidate/test CA, direct private 9444 | Not acceptable production trust | Separate production-CA `serverAuth` enrollment prepared; install after release gate |
| Event client certificate | production CA client leaf, serial `300D` | Valid and distinct from observer server identity | Preserve until renewal threshold |
| Renewal | absent | Fail-closed implementation, unit, timer, examples and tests added by this PR | Deploy after signed release and isolated renewal fixtures |
| 8443 | private Apache restricted gateway | Candidate common gateway | Architecture review required before observer migration |
| 8444 | no listener | Obsolete staging service | Keep retired |
| 9443 | private Apache compatibility gateway | One staging credential proxy dependency | Migrate and prove zero use before retirement |
| 9444 | private observer | Separately justified until verified identity propagation exists on 8443 | Retain compatibility; never expose publicly |

## Release gate

PR 11 head must receive exact-head CI and an approval from a reviewer whose repository permission satisfies branch protection. A read-only external approval is useful review evidence but does not satisfy GitHub's protected-branch rule. No administrator bypass, feature-branch deployment, live-server build or self-approval is permitted.
