# Adapter implementation

Implemented in `src/codestra_vicidial`:

- strict request models with unknown-field rejection and business-unit list ranges;
- explicit canonical-to-eight-character VICIdial campaign mapping;
- HMAC-SHA256 identity, scope, timestamp, nonce, body binding, and source checks;
- no generic SQL, shell, or file operations;
- parameterized, fixed-query MariaDB repository with loopback enforcement;
- durable idempotency, hashed audit records, external mappings, and transfer policies;
- disabled create/adopt/read-back for campaigns, lists, user/inbound/closer groups,
  scripts, and dispositions;
- disabled agent synchronization/deactivation;
- duplicate-safe lead publication and callbacks;
- same-campaign transfer target policy;
- global and per-family kill switches, all default false;
- no production activation endpoint.
- existing compatible user groups are adopted only after strict
  `allowed_campaigns` normalization and ownership checks; missing membership is
  added without removing unrelated authorized campaigns;
- provisioning completion compares campaign, lists, user/inbound/closer groups,
  scripts, dispositions, callback policy, transfer policy, recording-policy
  reference, reporting mapping, and disabled state;
- a mismatch returns `reconciliation_required`, preserves the pre-write
  snapshot, hashes desired/actual evidence, and remains retryable.

The code is complete for offline/staging acceptance. It has not replaced the
running adapter and has not written to VICIdial.

Confirmed integration configuration:

- repository: `https://github.com/appolon1908-hue/codestra-production-platform.git`
- deployment branch: `release/vicidial-adapter`
- authorized source: `65.109.65.169`
- middleware: `https://api.codestra.agency`
- identity issuer: `https://auth.codestra.agency`

These settings do not bypass HMAC/mTLS, scope, feature-flag, test, or production
gates. Repository publication remains blocked pending all required checks.
