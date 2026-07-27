# VICIdial Restricted Adapter Staging Deployment

Generated UTC: 2026-07-27.

## Result

`STAGING_ADAPTER_DEPLOYMENT=PASS`

The curated `f9f91e8` release is deployed on Server B through the established
private Apache mTLS ingress:

```text
10.40.0.1
  -> edge.internal.codestra.agency
  -> 10.40.0.2:8443 (Apache mTLS)
  -> 127.0.0.1:8097 (restricted adapter)
```

There is no listener or firewall opening on port 9443. Port 9443 is closed on
the public address. MariaDB remains bound to `127.0.0.1:3306`.

## Preconditions

- Active Asterisk calls: 0
- Active Asterisk channels: 0
- Live VICIdial agents: 0
- Curated package tests: 13 passed
- Apache configuration: syntax valid
- systemd unit verification: passed
- systemd security exposure: 1.5 (`OK`)

## Backup and rollback

The protected runtime backup is:

```text
/opt/codestra/backups/vicidial-restricted-adapter-staging-20260727T215603Z
```

It contains the source release, systemd definitions, Apache configuration,
protected environment, symlink target, and firewall snapshots. The backup
contains runtime-sensitive material and is intentionally excluded from Git.
Its 2,808 checksum entries verified successfully.

A ten-minute automatic rollback was armed before service deployment and
cancelled only after all acceptance checks passed.

## Runtime safety

- Dedicated account: `codestra-adapter`
- Bind: loopback only
- Restart: on failure
- Graceful stop: SIGTERM, 30 seconds
- Memory limit: 256 MiB
- CPU quota: 50%
- Task limit: 64
- Capabilities: empty
- Filesystem and kernel protections: enabled
- Logs: structured journal records
- Readiness timer: enabled and active

All mutation flags, provisioning, agent sync, lead publication, callback,
transfer, and production-dialing flags are false. The ready endpoint confirms
`writes_enabled=false`.

No campaign write was performed.
