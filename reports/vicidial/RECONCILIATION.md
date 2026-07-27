# Reconciliation

The adapter exposes reconciliation inventory and retains canonical-to-actual
campaign, lead, and callback mappings in local durable state. Every mutation is
read back before success is recorded.

Cross-system desired-versus-actual comparison remains middleware-owned and was
not asserted because Odoo and n8n records were not directly accessed from this
dedicated telephony host. Destructive auto-repair is not implemented.
