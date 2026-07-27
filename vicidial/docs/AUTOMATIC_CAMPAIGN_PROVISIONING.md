# Automatic Campaign Design and Provisioning

## Objective

Every new Odoo call-center campaign must automatically receive a complete, canonical integration design.

The automation must create a design preview when the campaign is saved and provision disabled resources only after the campaign is approved.

The process must be idempotent, versioned, auditable, reversible, and safe to retry.

## Authority model

- Odoo is authoritative for the business campaign and desired state.
- Middleware is authoritative for integration mappings, provisioning state, policy validation, and orchestration.
- VICIdial is authoritative for actual telephony resources and call execution.
- n8n is an asynchronous automation consumer.
- The VICIdial adapter is the only component allowed to make controlled local VICIdial configuration changes.
- Odoo and n8n must never write directly to VICIdial tables.

## Lifecycle

```text
Odoo campaign created
→ Odoo writes transactional outbox event
→ middleware creates design preview
→ middleware validates identifiers and policies
→ supervisor reviews or Odoo auto-approval policy evaluates
→ Odoo campaign approved
→ middleware creates immutable provisioning plan
→ middleware sends a signed command to the VICIdial adapter
→ adapter creates or updates disabled VICIdial resources locally
→ adapter performs read-back
→ middleware compares desired and actual state
→ middleware creates or updates n8n workflow scope
→ middleware writes provisioning results back to Odoo
→ synthetic acceptance tests run
→ authorized activation changes the campaign from disabled to active
```

Creating an Odoo campaign must never immediately enable public dialing.

## Odoo campaign states

Use an explicit state machine:

```text
draft
→ design_pending
→ design_ready
→ approval_pending
→ approved
→ provisioning
→ provisioned_disabled
→ testing
→ staging_ready
→ activation_pending
→ active
```

Failure and retirement states:

```text
blocked
failed
rollback_pending
rolled_back
archived
```

## Automatic behavior

### On campaign creation

Odoo must:

1. Validate required business fields.
2. Assign or validate the business-unit code.
3. Assign an immutable integration UUID.
4. Write `campaign.design.requested.v1` to the transactional outbox.
5. Display provisioning status without waiting synchronously for VICIdial.
6. Prevent duplicate outbox events with a unique event key.

Middleware must automatically:

1. Read the campaign and business-unit policy.
2. Generate the canonical campaign ID.
3. Reserve list IDs from the correct business-unit range.
4. Generate user-group, inbound-group, closer-group, script, disposition, callback, reporting, and n8n scope designs.
5. Save a versioned design manifest.
6. Return the preview to Odoo.
7. Keep all production flags false.

### On campaign approval

Middleware must:

1. Freeze the approved design revision.
2. Validate that no identifier belongs to another business unit.
3. Validate that no resource would collide with an incompatible VICIdial resource.
4. Create a backup/snapshot instruction.
5. Send the provisioning command to the restricted VICIdial adapter.
6. Require idempotency and read-back.
7. Create or update n8n workflows in inactive state.
8. Update Odoo with actual resource identifiers and test state.
9. Run synthetic acceptance tests.
10. Leave the campaign disabled until activation is separately approved.

### On campaign change

Do not silently mutate active production resources.

1. Generate a new design revision.
2. Produce a desired-versus-actual diff.
3. Classify changes as safe, disruptive, or destructive.
4. Require approval for disruptive changes.
5. Create rollback data before applying.
6. Preserve prior revisions.

### On campaign archive

- Disable new dialing.
- Remove agents from active assignment according to policy.
- Preserve call history, leads, recordings references, dispositions, audit, and reports.
- Do not delete historical VICIdial records.

## Required input fields

A campaign cannot be approved without:

- Business unit
- Campaign name
- Purpose
- Direction: inbound, outbound, or blended
- Environment
- Default language
- Time zone
- Calling hours
- Consent policy
- Do-not-call policy
- Recording policy
- Default lead source policy
- Agent roles
- Transfer roles
- Callback policy
- Appointment policy
- Required disposition family
- Script template
- n8n automation template
- Reporting category
- Owner
- Supervisor
- Activation policy

Optional fields:

- DID
- IVR menu
- Carrier/trunk policy
- Predictive-dialing policy
- Hopper target
- Pacing policy
- Email/SMS/calendar templates
- AI assistance policy

## Canonical identifiers

Campaign:

```text
<BU>-<PURPOSE>-<DIRECTION>
```

Examples:

```text
COD-WEB-OUT
MOY-CARRIER-OUT
SCP-PRODUCT-IN
MBL-RENEWAL-OUT
```

Groups:

```text
<BU>_<PURPOSE>_SDR
<BU>_<PURPOSE>_CLOSERS
<BU>_<PURPOSE>_SUPPORT
<BU>_<PURPOSE>_RETENTION
<BU>_<PURPOSE>_SUPERVISORS
```

Script:

```text
<BU>_<PURPOSE>_<ROLE>_V<VERSION>
```

n8n scope:

```text
<ENV>-<BU>-<PURPOSE>-V<VERSION>
```

## Business-unit list ranges

```text
MOY: 11000-11999
COD: 21000-21999
SCP: 31000-31999
MBL: 41000-41999
RLP: 51000-51999
FTP: 61000-61999
TRX: 71000-71999
CAL: 81000-81999
TEST/STAGING: 91000-91999
```

List allocation must use a database lock or equivalent atomic reservation. Never calculate the next ID by an unsafe `MAX()+1` without concurrency protection.

## Generated design manifest

Middleware must persist a canonical JSON document similar to:

```json
{
  "schema_version": "campaign-provisioning.v1",
  "environment": "production",
  "integration_uuid": "immutable-uuid",
  "design_revision": 1,
  "business_unit": "COD",
  "odoo": {
    "campaign_id": 123,
    "campaign_code": "COD-WEB-OUT",
    "crm_team_code": "COD_PRIMARY",
    "owner_user_id": 10,
    "supervisor_user_id": 12
  },
  "vicidial": {
    "campaign_id": "COD-WEB-OUT",
    "active": false,
    "default_list_id": 21001,
    "lists": [
      {
        "list_id": 21001,
        "code": "COD_WEB_PRIMARY_001",
        "active": false
      }
    ],
    "user_groups": [
      "COD_WEB_SDR",
      "COD_WEB_CLOSERS",
      "COD_WEB_SUPPORT",
      "COD_WEB_SUPERVISORS"
    ],
    "inbound_groups": [
      "COD_WEB_CLOSERS",
      "COD_WEB_SUPPORT"
    ],
    "scripts": [
      "COD_WEB_SDR_V1",
      "COD_WEB_CLOSER_V1"
    ],
    "disposition_set": "COD_WEB_OUT_V1"
  },
  "n8n": {
    "scope": "PROD-COD-WEB-V1",
    "workflows_active": false
  },
  "policies": {
    "calling_hours": "policy-reference",
    "time_zone": "America/Santo_Domingo",
    "consent_policy": "policy-reference",
    "dnc_policy": "policy-reference",
    "recording_policy": "policy-reference",
    "transfer_policy": "same-campaign-only"
  },
  "feature_flags": {
    "lead_publication": false,
    "agent_sync": false,
    "live_call_control": false,
    "production_dialing": false
  }
}
```

Do not embed passwords, tokens, SIP credentials, or provider secrets in the manifest.

## Middleware data model

Implement equivalent records for:

### `campaign_blueprint`

- ID
- Code
- Business unit
- Purpose family
- Direction
- Required roles
- Required groups
- Script templates
- Disposition templates
- Callback template
- Appointment template
- n8n workflow templates
- Default policies
- Version
- Active

### `campaign_design_revision`

- Integration UUID
- Odoo campaign ID
- Revision
- Manifest hash
- Manifest
- Created by
- Created at
- Approval state
- Approved by
- Approved at

### `campaign_resource_allocation`

- Environment
- Business unit
- Resource type
- Reserved identifier
- Integration UUID
- Revision
- State
- Unique constraint

### `campaign_provisioning_run`

- Run ID
- Integration UUID
- Revision
- Idempotency key
- Desired manifest hash
- Status
- Current phase
- Started at
- Completed at
- Error class
- Error detail, redacted
- Rollback reference
- Evidence path

### `campaign_actual_resource`

- Run ID
- Resource type
- Desired identifier
- Actual identifier
- Desired hash
- Actual hash
- Read-back result
- Drift state
- Last verified at

### `campaign_provisioning_event`

- Event ID
- Run ID
- Event type
- Correlation ID
- Payload hash
- Created at
- Delivery state
- Retry count

## Events

Use versioned events:

```text
campaign.design.requested.v1
campaign.design.generated.v1
campaign.approved.v1
campaign.provision.requested.v1
campaign.provision.started.v1
campaign.resource.applied.v1
campaign.readback.completed.v1
campaign.provision.completed.v1
campaign.provision.blocked.v1
campaign.test.completed.v1
campaign.activation.requested.v1
campaign.activated.v1
campaign.rollback.requested.v1
campaign.rolled_back.v1
campaign.drift.detected.v1
```

## Required APIs

Implement equivalent versioned endpoints:

```text
POST /api/v1/campaign-designs/preview
GET  /api/v1/campaign-designs/{integration_uuid}
POST /api/v1/campaign-designs/{integration_uuid}/approve

POST /api/v1/campaigns/{integration_uuid}/provision
GET  /api/v1/campaigns/{integration_uuid}/provisioning-runs
GET  /api/v1/campaigns/{integration_uuid}/actual-state
POST /api/v1/campaigns/{integration_uuid}/test
POST /api/v1/campaigns/{integration_uuid}/activate
POST /api/v1/campaigns/{integration_uuid}/disable
POST /api/v1/campaigns/{integration_uuid}/rollback
POST /api/v1/campaigns/{integration_uuid}/reconcile
```

All state-changing endpoints require:

- Authenticated service or user
- Authorization
- Business-unit access
- Idempotency key
- Correlation ID
- Audit reason
- Feature-flag validation

## Provisioning phases

```text
VALIDATE
RESERVE_IDENTIFIERS
SNAPSHOT
CREATE_CAMPAIGN
CREATE_LISTS
CREATE_USER_GROUPS
CREATE_INBOUND_GROUPS
CREATE_SCRIPTS
CREATE_DISPOSITIONS
CREATE_CALLBACK_POLICY
CREATE_REPORTING_MAPPING
CREATE_N8N_SCOPE
READ_BACK
COMPARE
SYNTHETIC_TEST
COMPLETE_DISABLED
```

Each phase must be safely retryable.

## VICIdial adapter contract

The application server sends only a signed canonical manifest or narrowly scoped command.

The adapter must:

1. Authenticate the application server.
2. Verify source policy.
3. Verify schema and signature.
4. Verify idempotency.
5. Validate resource ownership.
6. Create a local backup of affected configuration.
7. Apply a transaction or compensating sequence.
8. Read the result from VICIdial.
9. Return normalized actual state.
10. Record evidence.
11. Never return database passwords.
12. Never expose general-purpose SQL execution.

Allowed command families:

```text
campaign.validate
campaign.provision-disabled
campaign.update-disabled
campaign.disable
campaign.readback
campaign.reconcile
agent.sync
agent.disable
lead.publish
callback.create
transfer-policy.sync
script.sync
disposition.sync
```

## Activation gates

A newly provisioned campaign remains disabled until:

- Odoo design revision is approved.
- Middleware mapping exists.
- VICIdial read-back matches.
- List allocation is correct.
- Groups exist.
- Scripts exist.
- Dispositions exist.
- n8n workflows exist but are inactive or safely scoped.
- Synthetic lead publication passes.
- Duplicate retry creates no duplicate.
- Test agent synchronization passes.
- Callback test passes when required.
- Transfer-policy test passes.
- Calling hours and time zone pass.
- Consent and DNC policies pass.
- Monitoring exists.
- Rollback is available.
- An authorized activation action is recorded.

## Idempotency

Use a stable key such as:

```text
<environment>:<integration_uuid>:<design_revision>:<operation>
```

The same request must return the existing run/result instead of creating duplicate resources.

## Drift management

A scheduled reconciler must compare desired and actual state.

Classify drift:

- `NONE`
- `SAFE_AUTO_REPAIR`
- `APPROVAL_REQUIRED`
- `CRITICAL`
- `UNKNOWN`

Never auto-repair destructive drift without approval.

## Tests

Required tests:

- Campaign creation produces one design event.
- Retrying the event produces one design revision.
- Concurrent list allocation never duplicates an ID.
- Invalid business-unit/campaign combinations are rejected.
- Existing compatible resources are adopted, not duplicated.
- Existing incompatible resources block provisioning.
- Provision retry creates no duplicate VICIdial resources.
- Adapter read-back matches middleware state.
- n8n workflows remain inactive before activation.
- Campaign activation is blocked when any required test fails.
- Campaign archive preserves history.
- Reconciliation detects intentional drift.
- Rollback restores the previous disabled state.
