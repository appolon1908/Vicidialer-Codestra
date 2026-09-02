# Rapid Campaign Synchronization

VICIdial is the telephony execution system, not the business authority. Odoo campaign creation/activation must reach VICIdial through Middleware and the restricted VICIdial adapter.

Canonical flow: Odoo -> Middleware durable command -> VICIdial adapter -> VICIdial -> status/read-back -> Middleware -> Odoo.

The adapter must support idempotent campaign create/update/suspend/retire semantics, status read-back, campaign-scoped agent/supervisor assignment, inbound group, dispositions, callbacks, scripts, transfers, recording policy, hours/timezone, lead source mapping and DNC/consent settings where supported.

Never write directly from Odoo into the VICIdial database. Do not blindly repeat an ambiguous create/update. Read back by stable campaign identity first.

Campaign identity must bind tenant_id, odoo_campaign_id and vicidial_campaign_id. External dialing remains disabled until a separate capability activation/canary passes. CALLS_PLACED must remain zero during source/staging certification unless explicitly authorized.
