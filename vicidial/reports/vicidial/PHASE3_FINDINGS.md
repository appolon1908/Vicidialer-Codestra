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
