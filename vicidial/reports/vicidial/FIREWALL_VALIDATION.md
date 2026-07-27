# Firewall validation

Result: **PRODUCTION_BLOCKED / configuration preserved**.

- `firewall-cmd --check-config`: success
- SSH and current telephony services remain allowed
- Existing private rules allow only `10.40.0.1 → 10.40.0.2` on 8443/8444
- No rule permits TCP/9443
- No process listens on TCP/9443
- MariaDB listens on loopback only

No firewall change was made. Before deployment, prepare timed automatic
rollback and add only an exact private-source rule for port 9443 after mTLS and
adapter configuration validation.
