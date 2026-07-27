# Rollback test

Offline compensating-rollback test: **PASS**.

An incompatible existing list was injected before provisioning. Preflight
blocked the operation and byte-equivalent serialized repository state confirmed
that no campaign, list, group, script, or disposition was added or changed.

A mandatory snapshot record is created before successful provisioning and its
identifier is returned with the read-back result.

Live MyISAM rollback remains **NOT_TESTED**. The production repository performs
precondition checks, creates only disabled resources, and retains disabled
partial state for reconciliation; a host-level database snapshot and approved
staging window are still mandatory before enabling writes.
