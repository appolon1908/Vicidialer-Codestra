# Pre-update service health

Captured 2026-07-27:

- Asterisk: active; zero active channels and calls
- MariaDB: active; bound to `127.0.0.1:3306`
- Active VICIdial campaigns: 9
- Live VICIdial agents: 0
- VICIdial auto calls: 0
- Calls in preceding 15 minutes: 0 outbound, 0 closer
- Apache configuration: syntax OK
- firewalld configuration: valid
- Existing event gateway: healthy on loopback; external delivery disabled
- Existing provisioning adapter: healthy on loopback
- Curated adapter unit: not deployed or started

No service was reloaded or restarted.
