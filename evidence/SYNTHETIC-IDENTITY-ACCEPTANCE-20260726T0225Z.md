# Synthetic identity acceptance — 2026-07-26T02:25Z

## Scope and safety result

One synthetic identity was exercised in isolated staging:

- Name: `Test Agent 20260726T0225Z`
- Odoo request: `PRV-2026-000040`
- Odoo employee: `MOY.2026.00006`
- Odoo user ID: `110`
- Keycloak username: `test.agent.20260726t0225z`
- Synthetic identity fixture: `fixture-user`
- Business unit: Transportation and Logistics staging
- Role: `AGENT`
- Odoo campaign: `MOY-STAGE-SYNTH`
- VICIdial staging mapping intended: `TEST_SYN`
- Inbound group intended: `TEST_IN`
- Reserved SIP extension: `6199`

This was not a real employee. All email addresses used the reserved,
non-deliverable `staging.invalid` domain. No production activation, external
delivery, or live dialing was enabled.

The request never entered `Active`. Mandatory adapters were unavailable, so
the drill failed closed, recorded five blocked steps, and executed controlled
partial rollback.

## Extension proof

Before reservation, extension `6199` had:

```text
VICIdial phones=0
VICIdial users/phone-login=0
VICIdial live agents=0
Asterisk configuration mentions=0
Asterisk PJSIP endpoint=0
Asterisk current contact=0
Asterisk chan_sip peer=0
Odoo assignments=0
Odoo identity links=0
Odoo active reservations=0
Provisioning-service state mentions=0
```

Extension `6101` was not used.

## Completed runtime checks

Odoo:

- Request created, submitted, approved, and identifier reservation executed.
- Operational and IT approval flags were recorded.
- Employee and disabled Odoo user were created.
- Employee ID, company email local part, Keycloak username, VICIdial username,
  and SIP extension were reserved.
- `6199` was transactionally reserved.
- Odoo user remained disabled and had no reusable local password set by the
  acceptance scripts.
- Call Center menu visibility passed under the synthetic user context.
- `MOY-STAGE-SYNTH` campaign visibility passed.
- unrelated `COD-STAGE-UNRELATED` campaign denial passed.
- supervisor visibility passed.
- Odoo and Keycloak step evidence was marked verified.
- eleven append-only provisioning audit rows were preserved.

Keycloak:

- A real staging Keycloak user was created disabled.
- Duplicate replay returned the same external identity.
- approved staging group assignment passed.
- `codestra_agent` realm-role assignment passed.
- Agent Desktop `agent` client-role assignment passed.
- Transportation staging campaign attribute assignment passed.
- `UPDATE_PASSWORD` remained required.
- `CONFIGURE_TOTP` remained required.
- disabled-identity verification passed.

The password and MFA actions were not completed because overall activation was
not permitted while mandatory downstream adapters were absent.

## Activation blocker

The deployed private provisioning service reported these degraded
capabilities:

```text
agent_desktop
email_provider
odoo
sip
vicidial
```

Its adapter configuration had only `keycloak.enabled=true`; all five adapters
above had `enabled=false`. The Odoo staging database also had
`auth_oauth=uninstalled`, so a passwordless Keycloak-backed Odoo login could
not be validated.

The central middleware remained fail closed:

```text
LIVE_WRITES_ENABLED=false
EXTERNAL_DELIVERY_ENABLED=false
ODOO_WRITE_ENABLED=false
VICIDIAL_READ_ENABLED=false
VICIDIAL_WRITE_ENABLED=false
VICIDIAL_CONTROL_ENABLED=false
WEBPHONE_STAGING_PROVISIONING_ENABLED=false
```

The VICIdial synthetic provisioning unit was inactive and its deployed
application directory was absent. No concrete mailbox-provider sandbox
adapter or independently running SIP provisioning API was available.

These controls were not bypassed. The request was never marked Active.

## Controlled rollback

Keycloak termination verification:

```text
enabled=false
lifecycle_state=terminated
```

Odoo final state:

```text
request=PRV-2026-000040
request_state=terminated
employee_active=false
employee_provisioning_state=terminated
odoo_user_active=false
odoo_identity_link=terminated/aligned
keycloak_identity_link=terminated/aligned
verified_compensated_steps=odoo,keycloak
blocked_steps=email,vicidial,sip,agent_desktop,verification
extension_6199=released
all_five_identifier_reservations=released
request_history_preserved=true
employee_history_preserved=true
audit_rows=11
```

Post-rollback external checks:

```text
Keycloak user count=1, enabled=false, lifecycle_state=terminated
VICIdial matching users=0
VICIdial extension 6199 phones=0
VICIdial extension 6199 live agents=0
Asterisk endpoint 6199=0
Asterisk registration/contact 6199=0
```

No mailbox, VICIdial user, SIP endpoint, or Agent Desktop session had been
created, so there was no untracked resource to delete or disable in those
systems.

## Required gates

```text
ODOO_USER_PROVISIONING_GATE=BLOCKED
KEYCLOAK_PROVISIONING_GATE=BLOCKED
EMAIL_PROVISIONING_GATE=BLOCKED
VICIDIAL_PROVISIONING_GATE=BLOCKED
SIP_PROVISIONING_GATE=BLOCKED
AGENT_DESKTOP_AUTHORIZATION_GATE=BLOCKED
MFA_GATE=BLOCKED
CAMPAIGN_ISOLATION_GATE=BLOCKED
SUSPENSION_GATE=BLOCKED
REACTIVATION_GATE=BLOCKED
TERMINATION_GATE=BLOCKED
RECONCILIATION_GATE=BLOCKED
SYNTHETIC_END_TO_END_GATE=BLOCKED
ROLLBACK_GATE=BLOCKED
```

The Odoo control-plane and disabled Keycloak subtests passed, and partial
rollback succeeded. The required gates remain `BLOCKED` because their complete
runtime acceptance definitions include activation and/or integrations that
were unavailable. No gate was promoted from a model, mock, or offline test.
