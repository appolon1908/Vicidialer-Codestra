# Staging adapter negative tests

| Test | Expected | Result |
|---|---:|---|
| No client certificate at private ingress | TLS rejection | PASS |
| Valid client certificate from unauthorized source | HTTP 403 | PASS |
| Invalid service identity/token | HTTP 403 | PASS |
| Missing authorization scope | HTTP 403 | PASS |
| Invalid HMAC signature | HTTP 403 | PASS |
| Replayed nonce | HTTP 403 after initial HTTP 200 | PASS |
| `/sql` | HTTP 404 | PASS |
| `/shell` | HTTP 404 | PASS |
| `/command` | HTTP 404 | PASS |
| `/exec` | HTTP 404 | PASS |
| `/file` and `/files` | HTTP 404 | PASS |

The authenticated positive request exercised only read-only reconciliation
against the memory backend. No VICIdial or MariaDB write was possible.
