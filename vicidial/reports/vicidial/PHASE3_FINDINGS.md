# VICIdial R3 Phase 3 review findings

Every status below was verified against source at Phase 3 start SHA
`93c44b5ec8189f0a9d8f021443b615c559cf113e`. Line numbers refer to the
remediated file; Git history retains the reviewed starting lines.

## Phase 3A

| Finding | Verification | Disposition | Evidence |
|---|---|---|---|
| Deployment definitions diverged | Confirmed | Modified: `vicidial/ops` is canonical and deployment copies have byte-parity tests | `tests/test_phase3a_release_network.py` |
| Production port contract was inconsistent | Confirmed | Modified: backend `127.0.0.1:8097`, Apache mTLS `10.40.0.2:8443`, peer `10.40.0.1`; deployable 9443 references forbidden | `config/vicidial-server.env.example`, `ops/apache/codestra-vicidial-restricted-adapter.include` |
| Deployment copy invoked `.venv/bin/uvicorn` | Confirmed | Modified: both copies use `.venv/bin/python -m uvicorn` | `ops/codestra-vicidial-restricted-adapter.service` |
| Certificate renewal used generic Python 3 | Confirmed | Modified: both invocations use `/usr/bin/python3.11` | `ops/certificate-renewal/codestra-vicidial-certificate-renewal.service` |
| Mutable feature branch was deployment authority | Confirmed | Modified: configuration requires immutable merged SHA and artifact digest | `config/vicidial-server.env.example` |
| Release manifest was incomplete/non-reproducible | Confirmed | Modified: complete sorted R3 file manifest with SHA-256 generation and verification | `scripts/generate_release_manifest.py` |
| Release identity components were not all cross-checked | Confirmed | Modified: source file, release directory, build info, version response, and manifest must agree | `scripts/verify_release_tuple.py`, `src/codestra_vicidial/release.py` |

## Phase 3B

| Finding | Verification | Disposition | Evidence |
|---|---|---|---|
| HMAC did not bind route, method, identity, scopes, or idempotency | Confirmed | Modified: signature v2 binds every requested field and the body digest | `src/codestra_vicidial/security.py`, `tests/test_phase3b_security.py` |
| Signature v1 remained usable | Confirmed | Modified: only signature version `v2` is accepted | `src/codestra_vicidial/security.py` |
| Known fallback key was available outside a named development profile | Confirmed | Modified: fallback exists only in `development-loopback`, whose source allowlist is loopback-only | `src/codestra_vicidial/app.py` |
| Reconciliation `protected_sha` was parsed but not enforced | Confirmed | Modified: policy SHA must equal verified loaded release SHA | `src/codestra_vicidial/reconciliation.py` |
| Backup verification trusted status strings | Confirmed | Modified: policy identifies an absolute regular artifact and binds its actual SHA-256 | `src/codestra_vicidial/reconciliation.py`, `tests/test_reconciliation_policy.py` |

## Phase 3C

| Finding | Verification | Disposition | Evidence |
|---|---|---|---|
| Callback external IDs permitted SQL pattern characters | Confirmed | Modified: IDs use a restricted literal alphabet and durable adapter mappings; SQL wildcard ambiguity is impossible | `src/codestra_vicidial/models.py`, `tests/test_phase3c_domains.py` |
| Callback offsets were discarded without UTC conversion | Confirmed | Modified: naive values are rejected and aware values normalize to UTC before storage | `src/codestra_vicidial/models.py`, `src/codestra_vicidial/repository.py` |
| Agent synchronization lacked durable ownership and complete readback | Confirmed | Modified: desired state and ownership are persisted only after all identity, disabled-state, campaign, and inbound-group checks pass | `src/codestra_vicidial/service.py`, `src/codestra_vicidial/state.py` |
| Campaign update endpoint silently reprovisioned | Confirmed | Modified: misleading unversioned update route removed | `src/codestra_vicidial/app.py`, `tests/test_adapter.py` |
| Script and disposition sync endpoints reprovisioned an entire campaign | Confirmed | Modified: misleading broad aliases removed; resources remain part of complete disabled provisioning | `src/codestra_vicidial/app.py`, `docs/VICIDIAL_ADAPTER_CONTRACT.md` |
| Campaign validation checked only three campaign fields | Confirmed | Modified: validation executes the exact complete, non-mutating provisioning preflight | `src/codestra_vicidial/repository.py`, `tests/test_phase3c_domains.py` |
| Lead lookup was named as idempotency lookup but used external IDs | Confirmed | Modified: route renamed to `by-external-id` | `src/codestra_vicidial/app.py` |
| Reconciliation GET recomputed state and accepted fake run IDs | Confirmed | Modified: POST persists an immutable run and GET returns only that stored run | `src/codestra_vicidial/state.py`, `tests/test_phase3c_domains.py` |
| `TEST_SYN` transfer ownership derived business unit `TEST_SYN` | Confirmed | Modified: the certified exception maps to business unit `TEST` | `src/codestra_vicidial/service.py`, `tests/test_phase3c_domains.py` |
