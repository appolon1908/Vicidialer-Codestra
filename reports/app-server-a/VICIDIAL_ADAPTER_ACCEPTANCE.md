# VICIdial Adapter Staging Acceptance

Generated UTC: 2026-07-27.

| Test | Expected | Result |
|---|---:|---:|
| Health from `10.40.0.1` with trusted client certificate | 200 | PASS |
| Ready from `10.40.0.1` with trusted client certificate | 200, writes false | PASS |
| Version from `10.40.0.1` with trusted client certificate | 200 | PASS |
| Request without client certificate | TLS rejected | PASS |
| Trusted certificate from unauthorized local source | 403 | PASS |
| Invalid service identity | 403 | PASS |
| Missing required scope | 403 | PASS |
| Invalid HMAC signature | 403 | PASS |
| Replayed nonce after successful read-only validation | 403 | PASS |
| Signed read-only campaign validation | 200 | PASS |
| `/sql` route | 404 | PASS |
| `/shell` route | 404 | PASS |
| `/command` route | 404 | PASS |
| `/exec` route | 404 | PASS |
| `/file` route | 404 | PASS |
| Public `65.21.67.207:9443` | closed | PASS |
| MariaDB network bind | loopback only | PASS |
| Post-deployment active calls | 0 | PASS |
| Post-deployment live agents | 0 | PASS |

The adapter uses mTLS plus service identity, scope, timestamp, nonce, and HMAC
signature. It does not expose a bearer-token-only authorization path.

`LIVE_CAMPAIGN_WRITES=NOT_PERFORMED`
