# VICIdial/Asterisk adapter completion

Date: 2026-07-29

## PR 1 — Private adapter API

Implemented signed, body-bound execution grants; exact target binding; private
source, identity, scope, timestamp, nonce, and request-signature checks; global
and per-operation flags; runtime target attestation; and narrow operation-result
readback. The adapter still has no general SQL, shell, AMI-action, or file API.

## PR 2 — Durability and recovery

SQLite WAL state now records stable operation IDs, attempts, lifecycle status,
redacted failure types, results, and interrupted operations requiring recovery.
Existing request/result idempotency, snapshots, audit hashes, evidence, mappings,
collision handling, compensating disable behavior, and reconciliation remain in
place.

## PR 3 — AMI/TLS event gateway

Added a bounded AMI parser, explicit read-only event allowlist, sensitive-field
redaction, deterministic event IDs, a durable deduplicating outbox, retry state,
TLS 1.2+ verified mutual-certificate delivery configuration, loopback-only AMI
runtime, and a hardened service unit. AMI actions are limited to Login, Events,
Ping, and Logoff.

## PR 4 — Staging, monitoring, and read-only acceptance

Added staging credential and target validation, private-ingress routes for
runtime and operation readback, alert rules for availability, unexpected write
enablement, observation gaps, and delivery backlog, plus a read-only acceptance
validator. All mutation and production-dialing flags remain false.

Offline unit result: see `ADAPTER_TESTS.md`. Live deployment and live-system
acceptance remain separately gated because this source workspace is not the
VICIdial host and no production mutation was authorized or attempted.

Recommendation: **STAGING-ONLY**.
