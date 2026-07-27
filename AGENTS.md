# AGENTS.md — Dedicated VICIdial Server

- Server: `65.21.67.207`
- Authorized application server: `65.109.65.169`
- Repository: `/root/codestra-vicidial-completion`

Rules:

- Preserve all working telephony.
- Begin with read-only inventory.
- Back up before mutations.
- Do not install Odoo, n8n, Keycloak, or main middleware here.
- Do not expose MariaDB remotely.
- The local adapter exposes only allowlisted operations.
- No general SQL, shell, or arbitrary file endpoints.
- Restrict adapter access to the authorized application server/private channel.
- Validate, authenticate, authorize, and audit every request.
- Every mutation is idempotent and read back.
- New campaign resources remain disabled.
- Existing compatible resources are adopted.
- Incompatible collisions block provisioning.
- Never delete historical calls, leads, users, or reporting data.
- Preserve SSH and telephony ports before firewall changes.
- Test rollback and record evidence.
