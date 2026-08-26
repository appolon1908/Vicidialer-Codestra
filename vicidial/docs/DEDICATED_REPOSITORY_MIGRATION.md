# Dedicated repository migration verification

The Codestra VICIdial integration history was extracted from
`appolon1908-hue/codestra-production-platform` into
`appolon1908-hue/Vicidialer-Codestra` on 2026-08-26.

The extraction preserves the VICIdial development, fix, baseline, release, and
production-activation branches; the Phase 2 VICIdial tag; commit topology,
authors, and messages; the restricted adapter; campaign-provisioning gateway;
deployment definitions; container packaging; security controls; tests; and
release evidence. Commit identifiers were rewritten because unrelated monorepo
paths were intentionally removed.

The active source-validation and exact-image container workflows are installed
on `main`. This documentation-only pull request exists to run both workflows in
the dedicated repository and prove that the filtered history remains buildable
and testable without modifying any runtime or production capability.

Migration is not deployment. Production remains governed by protected review,
an exact merged SHA, immutable image digest, signature verification, database
and host preflight, health and mTLS validation, monitoring, restart recovery,
and tested rollback. Dialing, callbacks, recording, lead publication, SMS,
email, and external delivery remain disabled until separately authorized.
