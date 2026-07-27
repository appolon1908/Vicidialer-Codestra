# MariaDB account and grant review

Review date: 2026-07-27. No account or grant was changed.

MariaDB binds only to `127.0.0.1:3306`. This prevents current remote access,
but host-scoped grants remain latent risks if the bind or firewall changes.

| Account | Scope/privilege summary | Probable owner | Risk | Separate remediation |
|---|---|---|---|---|
| `codestra_read@65.109.65.169` | `SELECT`, `SHOW VIEW` on `asterisk.*` | Application integration, ownership unconfirmed | High: directly contradicts the no-direct-MariaDB boundary if networking changes | Confirm owner and consumers; revoke/drop in separately backed-up change |
| `cron@%` | Global `RELOAD`; DML, SELECT and LOCK TABLES on `asterisk.*` | Legacy VICIdial cron, ownership unconfirmed | High: wildcard host and write access | Compare with `cron@localhost`; migrate consumers to localhost and remove wildcard after test |
| `custom@%` | `ALTER`, `CREATE` on `asterisk.*` | Legacy/custom VICIdial tooling, ownership unconfirmed | High: wildcard DDL | Identify process owner; replace with localhost least privilege |
| `slave@%` | replication, monitor, reload plus SELECT/SHOW VIEW | MariaDB replication, ownership unconfirmed | High: wildcard replication account | Confirm replica source IP and restrict exact host/TLS |
| anonymous local accounts | `USAGE` plus inherited/public test privileges | MariaDB defaults | Medium | Verify dependency, then remove in reviewed hardening change |
| `codestra-provisioning@localhost` | allowlisted VICIdial table DML and control-table DML | Existing provisioning adapter | Medium: includes DELETE on configuration/control tables | Retain pending owner review; confirm delete paths and reduce where possible |
| `codestra-telephony@localhost` | allowlisted read-only telephony tables | Event/reporting gateway | Low | Retain; periodically review table allowlist |
| rotated local sync accounts | read-only `asterisk`; DML/DDL on sync schema | Middleware sync | Medium | Confirm current account, expire superseded credential, reduce DDL after migrations |
| `root@localhost`, `mysql@localhost` | administrative | MariaDB administration | Expected privileged | Keep local; protect socket and credentials |

Production gate requirement: review ownership with service/process evidence,
capture grants and rollback SQL, test localhost replacements, then remediate one
account family at a time. Do not bundle grant removal into adapter deployment.
