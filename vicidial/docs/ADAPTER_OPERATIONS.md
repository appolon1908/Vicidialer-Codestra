# Restricted adapter operations

The adapter is not deployed automatically by this repository. The current
telephony services must remain running until a separately approved staging
change window.

## Boundaries

- Bind only to the approved private address. The sample unit uses `10.40.0.2`.
- Permit TCP/9443 only from `65.109.65.169` at the firewall.
- Terminate mTLS in the accepted private ingress or add it directly before
  production use. HMAC authentication remains mandatory in either case.
- MariaDB credentials are read from a root-managed file and the runtime rejects
  a non-loopback database host.
- Never enable a mutation family without `ENABLE_VICIDIAL_WRITES=true`.
- `ENABLE_PRODUCTION_DIALING` is hard-disabled by the current implementation.

## Kill switch

Set `ENABLE_VICIDIAL_WRITES=false` and restart only the adapter service. This
blocks provisioning, agent writes, lead publication, callbacks, and transfer
policy updates without restarting Asterisk, MariaDB, Apache, or VICIdial.

## Identifier constraint

This VICIdial schema defines `vicidial_campaigns.campaign_id` as `varchar(8)`.
Requests therefore carry both an immutable canonical business ID and an
explicit, reserved VICIdial actual ID of at most eight characters. The adapter
does not truncate or hash identifiers. Reuse with different ownership or
configuration returns a collision.

## MyISAM compensation

The affected VICIdial tables are MyISAM. Provisioning performs a precondition
check before the first insert, creates every resource disabled, and reads back
the result. A failure never deletes a row: partially created resources remain
disabled for reconciliation and safe retry. Host-level table/config snapshots
are required before enabling writes.
