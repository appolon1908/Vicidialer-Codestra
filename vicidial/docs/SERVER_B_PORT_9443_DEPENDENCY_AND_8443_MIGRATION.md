# Port 9443 dependency and 8443 migration

This document supplements `SERVER_B_CANONICAL_PORT_MIGRATION_8443.md` with the exact known 9443 dependency.

```text
PORT=9443
BIND_ADDRESS=10.40.0.2
PROCESS=Apache httpd-prefork
APACHE_VHOST=003-codestra-read-only-adapter-9443.conf
PURPOSE=historical read-only restricted-adapter proxy
CLIENTS=private Server A integration clients and an 8443 staging credential proxy
TLS_IDENTITY=/etc/codestra/pki/vicidial-mtls-gateway/server.crt
HMAC_REQUIREMENT=route-specific adapter authentication after mTLS
ENVIRONMENT=legacy/private compatibility
PUBLIC_EXPOSURE=denied
```

The live 8443 vhost currently proxies `/v1/staging/endpoint/6101/credential` to `https://10.40.0.2:9443/v1/staging/endpoint/6101/credential`. This exact path is the dependency that prevents retirement.

Resolution sequence:

1. Add the credential path directly to the reviewed 8443 restricted backend without widening its allowlist.
2. Use the staging identity and staging HMAC only; strip caller-supplied identity headers.
3. Test valid mTLS/HMAC plus wrong identity, missing/invalid HMAC, expiry and replay negatives.
4. Remove only the internal 8443-to-9443 proxy after correlated staging evidence passes.
5. Observe 9443 access logs for a reviewed zero-use window.
6. Disable the 9443 vhost and its private firewall allowance with automatic rollback armed.
7. Verify 8443 health, 9443 private/public denial, 9444 observer compatibility and reboot persistence.

Current disposition: `PORT_9443=TEMPORARY_COMPATIBILITY`. It is not a permanent exception and has no authority beyond the migration window. The release owner must either complete retirement or sign a time-bounded exception with owner, business reason, controls, expiry and removal plan.
