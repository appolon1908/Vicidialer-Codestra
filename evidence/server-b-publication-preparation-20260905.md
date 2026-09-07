# Server B publication preparation correction

Observed at 2026-09-05T23:53:37.074876+00:00.

PR #28 received independent latest-push approval from kazan555 at head
`a6d725b7ac0330f4d629bc6c1e48918f9eef2118`. All applicable source/container/security
checks passed and review threads were resolved. Normal protected squash merge
produced `95f775c9700cd026b96ede1d224dd92170314a01` at
2026-09-05T23:50:22Z. No administrator bypass was used.

The authorized main-only source publication was dispatched with
`PUBLISH_SERVER_B_SOURCE_RELEASE_9AC8EF48`:

- [Run 33999730332, attempt 1](https://github.com/appolon1908-hue/Vicidialer-Codestra/actions/runs/33999730332).
- Builder/workflow SHA: `95f775c9700cd026b96ede1d224dd92170314a01`.
- Selected runtime payload: `9ac8ef4840f78ba4ad9b816e4e409298505103ce`.
- Result: **FAIL**, completed 2026-09-05T23:51:03Z.
- Failed step: Build and test deterministic installer-compatible archive.
- Exact failure: `KeyError: 'build/lib/codestra_vicidial/__init__.py'`.
- Signing/upload: not reached; no verified published source artifact is claimed.

The newly added non-editable installation of the selected package generated a
`build/` directory inside that checkout. Those generated files have no trusted
Git mode. The archive builder correctly rejected them. Ordinary PR source CI had
installed its workflow checkout editably and never performed the publication's
selected-checkout dependency installation, leaving this preparation difference
untested.

The correction uses an editable dependency install for publication and performs
the same selected-checkout preparation in ordinary source CI before running the
exact archive/install tests. It does not exclude `build/`, relax unsafe-member or
untracked-file rejection, change source selection, or alter signature/provenance
gates. The extracted archive's actual non-editable package installation test is
preserved; editable preparation is not the production installer.

Local isolated reproduction used two clean detached worktrees of selected 9ac8ef
and Python 3.12.14, pip 26.2.1, pytest 8.4.2 in a disposable user-owned venv:

1. `python -m pip install "exact-source/vicidial[test]" pytest==8.4.2`
   reproduced the generated build directory and the exact builder failure.
2. `python -m pip install -e "exact-source/vicidial[test]" pytest==8.4.2`
   created no build directory; tracked source was unchanged; archive build and
   independent verification passed.
3. Archive/provenance tests with `SOURCE_RELEASE_TEST_ROOT` pointing at the
   prepared selected source: **25 passed in 5.09s**. These include installation,
   policy substitution/cleanup, permissions, safe members and signed-content
   binding regressions. This is unsigned local test evidence.

The successor requires independent review, current CI and normal protected merge
before another main-only publication. Downloaded archive signature/binding
verification and administrator deployment remain NOT_RUN. The effective
process/current mismatch and missing reviewed 6901 lifecycle/secret-authority
contract remain as recorded in server-b-source-correction-20260905.md. No runtime
symlink, venv, service, account, grant, credential, calling gate or production
configuration was changed; no calls were placed.
