# AMI lifecycle contract for Odoo projection

Protected `main` already contains the durable, loopback-only AMI lifecycle
gateway. This change does not add a second observer. It locks the exact event
namespace and payload fields consumed by Middleware for the Odoo screen-pop
projection.

The event gateway emits `codestra.vicidial.call.lifecycle.*` for created,
offered, ringing, answered, connected, held, resumed, transfer-started,
transfer-completed, hangup, completed, failed, and missed states. Events carry
call, Linkedid, Asterisk Uniqueid, campaign, agent, extension, tenant/business
unit, Keycloak subject, sequence, direction, and bounded call metadata.

The observer remains read-only. It cannot originate calls, execute AMI or ARI
commands, modify campaigns, write to Odoo, or enable production dialing. Voice
audio remains on the SIP/RTP path in Asterisk; only JSON lifecycle state is
published to Middleware.
