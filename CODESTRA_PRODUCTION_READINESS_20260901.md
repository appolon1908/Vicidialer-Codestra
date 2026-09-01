# Codestra Production Readiness Gate — VICIdial

Status: NOT PRODUCTION CERTIFIED

Governed by `Infustruction-repo/CODESTRA_PRODUCTION_READINESS_WAVE_20260901.md`.

Required: exact-head CI; Critical=0; High=0; private restricted adapter; campaign/tenant isolation; no broad admin credential use; idempotent call commands; unknown-outcome read-back before retry; recording/audit controls; observability; database backup/restore; staging call-simulation/sandbox proof; immutable release; rollback; production read-back.

Keep PRODUCTION_DIALING and CALLBACK_DISPATCH disabled until separately certified. Do not modify SSH access.
