# VICIdial baseline — 2026-07-27

## Safety state

- Confirmed repository target:
  `https://github.com/appolon1908-hue/codestra-production-platform.git`.
- Confirmed deployment branch: `release/vicidial-adapter`.
- Confirmed authorized application server: `65.109.65.169`.
- Confirmed middleware base URL: `https://api.codestra.agency`.
- Confirmed Keycloak issuer: `https://auth.codestra.agency`.
- These values are recorded as configuration metadata only. No remote was
  contacted, no branch was created, and no deployment was initiated.
- Inventory was read-only. No Asterisk, MariaDB, Apache, VICIdial, firewall, or
  active Codestra service was restarted or reconfigured.
- A pre-implementation backup is stored outside Git at
  `/opt/codestra/backups/vicidial-completion-preimplementation-20260727T200500Z`.
  Its repository archive and pre-existing edge-worktree patch passed SHA-256
  verification.
- The requested repository had no `.git` directory at discovery time.
- The related `/root/codestra-vicidial-edge` worktree was at
  `3f900fc45215164fec67cdf2043d294e566b3b4d` with extensive pre-existing
  modifications; it was not changed by this implementation.

## Platform and health

- Host: openSUSE Leap 15.6, kernel `6.4.0-150600.23.103-default`.
- Asterisk: `18.26.4-vici`; active, zero active calls/channels at inventory.
- MariaDB: `10.11.15`; active and listening only on `127.0.0.1:3306`.
- Apache, firewalld, fail2ban, and VICIdial worker processes were active.
- AMI listens on `127.0.0.1:5038`.
- Existing private provisioning adapter listens on `127.0.0.1:8098`.
- Existing event gateway health endpoint listens on `127.0.0.1:9478` and
  returned `{"status":"live"}`. External delivery is disabled.
- SSH listens on TCP/22. SIP/IAX/WebRTC/HTTP listeners were preserved.

## Inventory counts

| Resource | Count |
|---|---:|
| Campaigns | 25 |
| Lists | 22 |
| Users | 44 |
| User groups | 19 |
| Inbound groups | 31 |
| Scripts | 12 |
| Campaign dispositions | 86 |
| Callbacks | 2 |
| Phones | 31 |
| DIDs | 3 |
| Recording references | 23 |

All inspected provisioning tables (`vicidial_campaigns`, `vicidial_lists`,
`vicidial_users`, `vicidial_user_groups`, `vicidial_inbound_groups`,
`vicidial_scripts`, `vicidial_campaign_statuses`, `vicidial_callbacks`,
`vicidial_list`, and `phones`) use MyISAM.

## Existing integration

The live provisioning adapter supports agent and phone lifecycle operations,
signed requests, idempotency, and reconciliation. It does not expose the
campaign/list/group/script/disposition/lead/callback/transfer-policy contract.
The event gateway is containerized, read-only, least-capability, loopback-only,
durably stateful, and has external delivery disabled.

## Existing staging resources

Eight disabled staging campaigns exist with disabled lists in `91001–91008`,
disabled inbound groups, user groups, scripts, and test dispositions. They use
eight-character VICIdial IDs because the schema cannot store the longer
canonical campaign identifiers.

## Not safely discoverable on this host

Odoo campaign records and n8n workflow references are remote-system state and
were not queried through direct database access. Their mapping remains a
middleware acceptance dependency.
