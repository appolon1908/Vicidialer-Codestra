# Automatic campaign tests

Offline result: **PASS (13 tests)**.

Covered:

- strict schema and business-unit list ranges
- disabled-by-default campaign, list, group, and script resources
- idempotent retry without duplicates
- compatible adoption
- incompatible collision rejection
- pre-mutation collision/rollback safety
- mandatory pre-provision snapshot record
- campaign/list read-back
- agent synchronization and disable
- lead publication and duplicate/collision behavior
- callback creation/read-back
- same-campaign transfer target policy
- unauthorized/missing transfer target rejection
- reconciliation inventory

Live MySQL provisioning was deliberately not executed because write and
provisioning flags remain false.
