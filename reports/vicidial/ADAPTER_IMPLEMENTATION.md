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

The code is complete for offline/staging acceptance. It has not replaced the
running adapter and has not written to VICIdial.
