# AGENTS.md — Application Server A

- Server: `65.109.65.169`
- Peer VICIdial server: `65.21.67.207`
- Repository: `/root/codestra-production-completion`

Read `CODEX_APP_SERVER_A.md` and all referenced files first.

Rules:

- Implement and test; do not only advise.
- Preserve compatible existing work.
- Do not discard unrelated changes.
- Never commit or print secrets.
- Odoo and n8n never write directly to VICIdial.
- Middleware is the operational gateway.
- The VICIdial adapter performs restricted local mutations.
- New campaigns generate a design preview automatically.
- Approved campaigns provision disabled resources.
- Activation is separate and gated.
- Every mutation is idempotent and read back.
- Every PASS has evidence.
- Databases and Redis are private.
- Production flags begin false.
- Backups, restore proof, monitoring, and rollback are required before live activation.
