# Operational health profiles

`scripts/verify_operational_contract.py` is invoked by both the restricted
adapter's systemd startup gate and its periodic health service. It must accept
the same read-only production profile as `validate_runtime.py` and
`production_app.build_readiness()`.

`production-candidate` is now accepted only with a ready MySQL backend, matching
release SHA, a `production-candidate` build environment, verified release
metadata, and all dependency checks explicitly
true. Writes, internal calling, production dialing, customer traffic, and
reconciliation authorization must be explicitly false. The verifier requires
the response's actual deployment profile; it no longer substitutes the expected
profile when the response omits it.

The other supported profiles retain their existing policy checks. An inactive
or expired `production-external-one-call` policy still fails. Changing the
health verifier neither enables dialing nor recovers a stuck event queue.

This fixes the read-only profile's startup/health incompatibility from platform
issue #259. Deploy only the verified immutable artifact built from reviewed
protected source. Do not patch an installed release or reset its health failure
without correcting and rechecking the underlying runtime state.

Regression command:

```sh
python -m pytest -q vicidial/tests/test_operational_candidate.py \
  vicidial/tests/test_r3_phase2.py vicidial/tests/test_validate_runtime_profiles.py
```
