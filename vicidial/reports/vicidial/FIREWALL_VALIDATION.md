# Firewall validation

Result: **PRODUCTION_BLOCKED / configuration preserved**.

- `firewall-cmd --check-config`: success
- SSH and current telephony services remain allowed
- Existing private rules allow only `10.40.0.1 → 10.40.0.2` on 8443/8444
- No rule permits TCP/9443
- No process listens on TCP/9443
- MariaDB listens on loopback only

No firewall change was needed. The adapter is loopback-only behind the accepted
Apache mTLS listener. The existing exact `10.40.0.1 → 10.40.0.2:8443` rule is
the complete ingress path. TCP/9443 remains closed with no listener or rule.
A timed rollback was prepared and cancelled after successful validation.
