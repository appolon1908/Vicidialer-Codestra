# Sensitive artifact review

Result: **PASS for the curated repository candidate**.

No recordings, Asterisk spool content, database dumps, SQL data exports,
archives, backups, private keys, customer lead exports, runtime logs, dependency
directories, or files larger than 5 MiB are publication candidates.

The complete file inventory is `WORKING_TREE_INVENTORY.csv`; automated findings
are in `SENSITIVE_ARTIFACT_FINDINGS.csv`. The isolated `.venv`, caches,
generated egg metadata, and runtime environment file are ignored.
