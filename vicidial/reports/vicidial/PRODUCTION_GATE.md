# Production gate

Recommendation: **STAGING-ONLY / NO-GO for production**.

Publication target is
`https://github.com/appolon1908-hue/codestra-production-platform.git` on
`release/vicidial-adapter`. Staging, commit, push, and deployment remain blocked
until repository scope, secret/sensitive-artifact scans, the complete test
matrix, and every production gate pass. Force-push is prohibited.

Passing:

- working telephony preserved;
- MariaDB and AMI are loopback-only;
- baseline and new offline tests pass;
- adapter operations are allowlisted, authenticated, idempotent, audited, and
  read back;
- all mutation flags default false;
- production activation is absent and hard-disabled.

Blocking:

- new adapter has not been deployed through approved private mTLS ingress;
- source-only TCP/9443 firewall rule and rollback have not been exercised;
- MyISAM table snapshot/isolated restore rehearsal is pending;
- live staging synthetic campaign/lead/agent/callback/transfer tests are pending;
- event lifecycle restart/no-gap evidence is pending;
- authoritative Odoo/n8n mapping comparison is pending.
- repository destination and branch were verified before the checkpoint;
- candidate-tree, 53-commit history, staged-diff, and curated sensitive-artifact
  publication gates pass.

Do not enable production dialing.

Current critical blockers:

- `auth.codestra.agency` has no public A record; Keycloak public DNS gate is
  blocked.
- Curated adapter is not deployed and TCP/9443 is closed.
- Historical MariaDB remote/wildcard grant rows require separate review even
  though MariaDB is loopback-only.
- Required live synthetic adapter, event lifecycle/restart, cross-system
  reconciliation, and MyISAM rollback tests are incomplete.
- Merge and deployment remain prohibited while these gates remain incomplete.

Staging deployment of the loopback-only, memory-backed adapter is complete with
every write flag false. This does not change the recommendation:
**STAGING-ONLY / NO-GO for production**.

Positive health from `10.40.0.1`, live MyISAM rollback, event restart/no-gap
testing, live database-backed reconciliation, cross-system reconciliation, and
production dialing remain blocked or not tested.

The following remain explicitly false:
`ENABLE_VICIDIAL_WRITES`, `ENABLE_AGENT_SYNC`, `ENABLE_LEAD_PUBLICATION`,
`ENABLE_LIVE_CALL_CONTROL`, `ENABLE_WARM_TRANSFER`, `ENABLE_IVR_ROUTING`,
`ENABLE_PRODUCTION_DIALING`, `ENABLE_EMAIL_DELIVERY`,
`ENABLE_SMS_DELIVERY`, and `ENABLE_AI_ACTIONS`.
