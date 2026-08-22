# Restricted VICIdial integration

Server: `65.21.67.207`

This component contains the source-only VICIdial adapter baseline. It does not
install Odoo, n8n, Keycloak, or the main middleware service and does not deploy,
reload, restart, provision, or activate the live phone system.

## Implemented adapter

The restricted adapter source is in `src/codestra_vicidial`. It implements the
allowlisted campaign, agent, lead, callback, script, disposition, transfer
policy, and reconciliation contract. All write flags default to false and there
is no production activation route.

Run the offline suite from this directory:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

The repository's existing top-level `tel_events/` package remains the compatible
event-gateway source; it is not duplicated here. See
`docs/ADAPTER_OPERATIONS.md` and `reports/vicidial/PRODUCTION_GATE.md` before a
separately approved staging deployment.

The temporary `production-reconciliation` profile exposes only the dedicated
`telephony:reconcile` disabled-campaign endpoint. Every request must match the
root-owned immutable policy file's exact campaign hash, plan digest, owner
authorization reference, and validated backup reference. Backup evidence must
be less than 24 hours old at both service startup and request time. Generic
campaign, script, and disposition mutations remain denied in this profile.
