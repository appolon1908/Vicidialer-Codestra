# Adapter trust configuration

The preferred route is the existing private VLAN:

- Application-side peer observed: `10.40.0.1`
- VICIdial private address: `10.40.0.2`
- Authorized public application identity: `65.109.65.169`
- Proposed adapter bind: `10.40.0.2:9443`
- Middleware: `https://api.codestra.agency`
- Keycloak issuer: `https://auth.codestra.agency`

Existing Apache private ingress on `10.40.0.2:8443` requires a client
certificate and restricts requests to `10.40.0.1`. A request without a client
certificate was rejected during validation.

The adapter additionally requires exact source validation, service identity,
scope, timestamp, nonce, and HMAC signature. Port 9443 is not currently
listening and has no firewall allow rule.

MariaDB remains loopback-only. Historical grant rows for a direct application
address and wildcard accounts exist; they are not reachable while the loopback
bind holds, but must be reviewed and remediated in a separate database-access
change before production.
