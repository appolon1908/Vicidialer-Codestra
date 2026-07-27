# VICIdial MariaDB Access Review

Generated UTC: 2026-07-27.

This is an inventory only. No account or grant was changed.

MariaDB listens only on `127.0.0.1:3306`, so historical remote grants are not
currently reachable over the network. They still require ownership review.

| Account | Host | Observed privilege summary | Ownership | Recommendation |
|---|---|---|---|---|
| `codestra_read` | `65.109.65.169` | `SELECT`, `SHOW VIEW` on `asterisk` | Codestra integration, historical | Review and replace with private/local adapter access; do not revoke automatically |
| `cron` | `%` | DML and lock privileges on `asterisk` | Unknown | Identify VICIdial job owner and narrow host after reviewed compatibility test |
| `custom` | `%` | `ALTER`, `CREATE` on `asterisk` | Unknown | Identify application owner; wildcard host is not acceptable long term |
| `slave` | `%` | Global `SELECT` observed | Unknown/replication candidate | Confirm replication use and narrow host/privileges |
| anonymous-style account | server hostname | No global privilege observed | Unknown | Review authentication and remove only through separate approved change |
| `PUBLIC` | empty host | Test-schema privileges | MariaDB default/test compatibility | Review separately; not part of adapter deployment |

The local rotated sync account retains scoped access on loopback and is outside
the remote/wildcard remediation set.

## Separate reviewed change requirements

1. Map each account to an owner and active process.
2. Capture `SHOW GRANTS`, authentication plugin, last-use evidence, and
   dependency tests without recording password hashes.
3. Back up grants and prepare per-account rollback statements.
4. Replace wildcard/public-host access with loopback or exact private sources.
5. Test VICIdial cron, reports, replication, carrier, and administrative paths.
6. Revoke or lock only after explicit approval.

`DATABASE_GRANTS_CHANGED=NO`
