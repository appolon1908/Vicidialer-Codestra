# Staging adapter deployment

Deployed 2026-07-27 on VICIdial server `65.21.67.207`.

## Runtime

- Service: `codestra-vicidial-restricted-adapter.service`
- Runtime account: `codestra-adapter`
- Release: `/opt/codestra/vicidial-restricted-adapter/releases/f9f91e8`
- Listener: `127.0.0.1:8097`
- Private ingress: Apache mTLS on `10.40.0.2:8443`
- Allowed ingress peer: `10.40.0.1`
- Authorized public application identity: `65.109.65.169`
- Backend: memory
- Health: PASS
- Readiness: PASS, `writes_enabled=false`
- Automatic restart: enabled
- Health timer: enabled
- Logs: systemd journal with service identifier and journal rotation

The accepted Apache private ingress is used instead of binding the application
to `10.40.0.2:9443`. There is no public or private 9443 listener or firewall
rule.

All mutation, provisioning, agent-sync, lead-publication, callback, transfer,
and production-dialing flags are false. No VICIdial campaign write was run.

## Safety and rollback

Pre-change backup:
`/root/codestra-vicidial-deploy-backups/20260727T213745Z`

The backup contains source, systemd, Apache, and firewalld state with 21
verified checksums. A timed firewall rollback was prepared before deployment
and cancelled after validation because no firewall rule was added.

Apache configuration was syntax checked before a graceful Apache reload.
Asterisk and MariaDB were not restarted or reloaded. There were zero active
agents, calls, and Asterisk channels before and after deployment.

## Remaining gate

Positive mTLS health from `10.40.0.1` is BLOCKED because this host has no
authenticated execution channel on Application Server A. Local loopback health
and readiness pass, and Apache rejects both missing-certificate and
wrong-source requests.
