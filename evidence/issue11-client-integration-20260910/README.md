# Issue #11 client integration verification

Tested implementation commit: `34bbdee11e1acd955229acaf1f399418474ce080`.

`python -m pytest -q vicidial/tests tests` passed **860 tests and 75 subtests**,
with **six environment-fixture skips**, on Python 3.12.11 in the isolated Core
worktree. Completed 2026-09-10T17:30:10.233250+00:00.
The new client HTTP contract suite includes 30 cases.

The original protected-main lifecycle failed against real signed HTTP handlers
with 422 for the five missing authority/version fields. The corrected lifecycle
and lookup recovery pass against real adapter services and temporary SQLite;
HTTPS and the telephony repository are simulated boundaries. The lost-response
case commits one mutation, recreates the local intent store, disables writes,
and recovers the exact result through lookup without another mutation.

See `validation.json` for source, command, timestamps and log checksum,
[the full test result](pytest.txt), and
[the integration map](../../vicidial/docs/ISSUE11_CLIENT_INTEGRATION.md) for
current Middleware contract differences and runtime prerequisites.
This evidence does not certify production MariaDB, live mTLS, Odoo/n8n delivery,
backup restoration, release activation or a call canary. No runtime mutation or
live call was performed. Final PR-head source and container checks are recorded
in GitHub after the evidence-only commit.
