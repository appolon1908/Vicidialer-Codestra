# Server B internal call execution contribution — 2026-09-05

Scope: source and synthetic regression work for the missing execution layer.
No production certification, deployment, activation, SIP registration, audio,
terminal live call, or customer-history result is claimed by this record.

Coordinated source: VICIdial PR #26, head
`0d28a4b6c4460547785eebece09ad5fa8cf9e6d3` (refreshed at 18:58 UTC).
The contribution incorporates that head and preserves its Middleware PR #145
HTTP paths, scopes, operation IDs and originate payload. It is a follow-up for
integration into #26, not a parallel execution API. Infrastructure PR #95 is not
reopened or recreated.

Implementation/test commit: `5a44c0d305c84aec72a2ff9fb0e3baaa0eee8d65`.
The enclosing evidence commit changes documentation only.

## Verification

Local environment: Server B source worktree, unprivileged codestra-admin,
Python 3.12.14 in an isolated user-owned test environment. Tests use synthetic
fixtures and temporary databases, fake transport or fake sockets. They do not
contact Asterisk, MariaDB, Odoo or Middleware production services.

2026-09-05 18:58 UTC, combined VICIdial and root source suites:

```text
CODESTRA_ADAPTER_STATE=<isolated test database> python -m pytest -q -rs vicidial/tests tests --basetemp=<isolated test directory>
404 passed, 6 skipped, 1 warning
```

Five skips require isolated MariaDB; one requires the built container image.
They are NOT PASS. One warning is Starlette's AnyIO deprecated alias.
The final commit additionally names the existing profile constant consistently
and explicitly sets subprocess check=False in the negative startup test;
GitHub exact-head checks must confirm the complete committed tree.

Ruff passed for internal_calling.py, internal_call_transport.py and their two
test modules. Source/script compilation, entrypoint shell syntax and Git
whitespace checks passed. Protected release and runtime checks were not run.

## Review findings and additional execution checks

- Dedicated profile required at application startup, shell entrypoint and
  runtime validator; readiness reports internal calling separately and checks
  the current policy. A memory backend cannot certify this production profile.
- One-call intent reservation is serialized with BEGIN IMMEDIATE. Distinct
  concurrent operation IDs cannot produce two calls. Existing unbound stores
  require administrator reconciliation; no automatic destructive migration.
- Existing StateStore audit schema receives correlated actor/outcome records,
  including pre-dispatch intent, malformed authenticated commands, denials,
  accepted operations, duplicates and failed/uncertain dispatches.
- Concurrent same-call hangup requires winning one atomic claim. Live Status
  must match the exact stored unique ID and PJSIP/6901 before AMI Hangup uses
  that unique ID. Other channels and regex targets are rejected.
- Root-owned policy is reopened on each request, bound to release and original
  operation, and denies expiry, revocation, replacement and unsafe file modes.
- Live read-only SQL checks appolon/6901 activation and TEST_SYN binding.
- Live ShowDialPlan must match the protected digest of the exact internal
  Answer/Echo/Hangup route 6000; drift, includes, patterns and other applications
  are rejected before originating.
- Readback uses the actual AMI LifecycleStore schema and rejects tenant/subject
  mismatches. Terminal success needs the corresponding terminal outbox event,
  end timestamp, and matching call identity. AMI acknowledgement is insufficient.
- The receive-only gateway remains unchanged; no generic AMI/SQL/shell endpoint
  or alternate Middleware authority is introduced.

## Remaining deployment boundaries

The policy's new route_sha256 must come from administrator-verified live route
evidence. Final combined-head review and protected artifacts are still required.
The administrator must verify backups/restore, rollback, current account policy,
loopback transport, dedicated AMI credentials and read-only lifecycle access.
SIP/browser enrollment, Temporal integration, human echo/audio/hangup and one
accurate Odoo history entry remain later steps. No live request was made here.

PRODUCTION_CHANGES=NONE
LIVE_CALLS_PLACED=0
RUNTIME_EXECUTION_AUTHORIZATION=NOT_ESTABLISHED_BY_SOURCE_TESTS
PROTECTED_ARTIFACT=NOT_PRODUCED
