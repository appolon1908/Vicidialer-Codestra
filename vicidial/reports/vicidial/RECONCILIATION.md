# Reconciliation

Offline adapter reconciliation: **PASS**.

The repository inventory is read back after synthetic provisioning and reports
the expected resource counts. The service returns a stable reconciliation
result through the narrow API.

Cross-system Odoo/middleware/n8n/live-VICIdial reconciliation remains
**NOT_TESTED** because mutation and synchronization flags are disabled and
public Keycloak DNS is unresolved.

The adapter exposes reconciliation inventory and retains canonical-to-actual
campaign, lead, and callback mappings in local durable state. Every mutation is
read back before success is recorded.

Cross-system desired-versus-actual comparison remains middleware-owned and was
not asserted because Odoo and n8n records were not directly accessed from this
dedicated telephony host. Destructive auto-repair is not implemented.

Provisioning read-back failures are not recorded as successful idempotency
results. They record redacted per-resource evidence, force mutable resources
disabled, and permit the same key to be retried after correction. A corrected
retry records `complete_disabled`.
