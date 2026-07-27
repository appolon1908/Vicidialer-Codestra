# Backup and restore evidence

Pre-implementation backup:

`/opt/codestra/backups/vicidial-completion-preimplementation-20260727T200500Z`

Contents include the handoff repository archive, a copy of the live adapter
application, the pre-existing edge-worktree patch, and `SHA256SUMS`. Directory
mode is `0700`; files are `0600`. `sha256sum -c` passed for the repository
archive and patch.

Restore rehearsal was non-destructive: the archive table was readable and the
checksums passed. No live database restore was attempted. Before enabling MyISAM
writes, take a fresh `mariadb-dump --single-transaction=false --lock-tables`
snapshot of only affected allowlisted tables plus protected Asterisk/VICIdial
configuration, verify its checksum, and rehearse restore into an isolated
database. Never restore over the live system during calls.
