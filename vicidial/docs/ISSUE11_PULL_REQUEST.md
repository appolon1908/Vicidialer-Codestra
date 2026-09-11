# Issue #11 implementation pull request

This pull request tracks the repository-side implementation for [production integration issue #11](https://github.com/appolon1908-hue/Vicidialer-Codestra/issues/11).

## Included source boundary

The protected `main` branch contains the restricted VICIdial adapter, campaign and agent isolation, signed and replay-protected commands, durable result lookup, fail-closed readiness, and bounded internal calling. The current protected heads used for coordination are:

- Vicidialer-Codestra: `573d1cc2f2cd6aa9ec2f016f1dc9c2e7f329b188`
- Middleware: `4a449410965bfd1c465ebc4173ecf5cdf2adf360`

The adapter remains private and allowlisted. Callers cannot provide arbitrary SQL, shell, dialplan, trunk, URL, or credential input. Production dialing and telephony administration remain disabled.

## Machine-readable binding

`vicidial/config/restricted-adapter-contract.json` is generated from the live
route, scope and request-model maps in `codestra_vicidial.app`. It is the
reviewed binding input for Middleware/Odoo clients: every protected route has
one explicit method, scope, request model and idempotency policy. The generator
refuses an unmapped POST route or a mutating non-POST route, and the regression
suite fails if the committed manifest drifts from the implementation.

Generate it with:

```text
python vicidial/scripts/export_adapter_contract.py
```

The client now also rejects durable receipts whose `recorded_at` is missing a
usable timezone or is more than 60 seconds in the future. This prevents a
malformed or clock-skewed lookup response from being treated as proof of a
committed mutation.

## Acceptance checklist

- [x] Campaign-scoped agent and user-group isolation is enforced in the adapter.
- [x] Commands are authenticated, authorized, idempotent, audited, and read back.
- [x] Replay and conflicting expected-version requests fail closed.
- [x] Health, readiness, source identity, and registration state are exposed without enabling writes.
- [ ] Middleware/Odoo private-route binding is verified against the deployed endpoint and certificate identity.
- [ ] Server B configuration and event backlog reconciliation is completed.
- [ ] Off-host backup, isolated restore, and rollback evidence is current.
- [ ] A separately approved synthetic canary produces durable CDR, disposition, duration, and hangup evidence.

The unchecked items are runtime release gates. This PR does not claim a production deployment or place a call; it keeps the issue open until those gates have independently recorded evidence.
