"""Synthetic, rollback-only acceptance executed inside the isolated Odoo shell."""
from odoo.exceptions import AccessError, UserError, ValidationError

results = []
def passed(name):
    results.append(f"PASS|{name}")

unit_transport = env.ref("call_center_core.business_unit_transport")
unit_digital = env.ref("call_center_core.business_unit_digital")
unit_loan = env.ref("call_center_core.business_unit_moneybee")
unit_senior = env.ref("call_center_core.business_unit_senior")
unit_shared = env.ref("call_center_core.business_unit_shared")

# Transportation workflow.
shipper = env["res.partner"].create({"name": "SYNTHETIC Shipper", "transport_role": "shipper"})
carrier = env["res.partner"].create({"name": "SYNTHETIC Carrier", "transport_role": "carrier"})
lane = env["codestra.transport.lane"].create({
    "name": "SYNTHETIC SDQ-MIA", "origin": "SDQ", "destination": "MIA",
    "business_unit_id": unit_transport.id, "idempotency_key": "accept-lane-1",
})
load = env["codestra.transport.load"].create({
    "name": "SYNTHETIC LOAD", "lane_id": lane.id, "shipper_id": shipper.id,
    "carrier_id": carrier.id, "business_unit_id": unit_transport.id,
    "accepted_rate": 1500, "cost_amount": 1100,
})
assert load.profitability == 400
assert env["codestra.transport.lane"].create({
    "name": "DUPLICATE", "origin": "X", "destination": "Y",
    "business_unit_id": unit_transport.id, "idempotency_key": "accept-lane-1",
}) == lane
passed("transportation_workflow_and_idempotency")

# Dedicated lending workflow with immutable decision history.
borrower_partner = env["res.partner"].create({"name": "SYNTHETIC Borrower"})
borrower = env["codestra.borrower.profile"].create({
    "partner_id": borrower_partner.id, "business_unit_id": unit_loan.id,
    "consent_state": "granted",
})
application = env["codestra.loan.application"].create({
    "name": "SYNTHETIC LOAN", "borrower_id": borrower.id,
    "requested_amount": 5000, "business_unit_id": unit_loan.id,
})
application.write({"application_state": "approved", "approved_amount": 4000})
decision = env["codestra.loan.decision.audit"].search([("application_id", "=", application.id)])
assert len(decision) == 1
try:
    decision.unlink()
    raise AssertionError("immutable decision was deleted")
except AccessError:
    pass
passed("loan_workflow_and_immutable_audit")

# Student repayment remains a separate model/table and fails closed on consent.
student = env["res.partner"].create({"name": "SYNTHETIC Student"})
servicer = env["res.partner"].create({"name": "SYNTHETIC Servicer"})
case = env["codestra.student.servicer.case"].create({
    "name": "SYNTHETIC CASE", "student_id": student.id, "servicer_id": servicer.id,
    "business_unit_id": unit_shared.id,
})
assert case._table != application._table
assert case.consent_state == "unknown" and case.dnc_state == "unknown"
passed("student_separation_and_fail_closed_consent")

# Services sale confirmation creates exactly one project on repeated delivery.
lead = env["crm.lead"].create({"name": "SYNTHETIC Services Lead", "business_unit_id": unit_digital.id})
intake = env["codestra.service.intake"].create({
    "name": "SYNTHETIC Web Project", "lead_id": lead.id, "service_type": "web",
    "business_unit_id": unit_digital.id,
})
client = env["res.partner"].create({"name": "SYNTHETIC Client"})
order = env["sale.order"].create({"partner_id": client.id, "codestra_service_intake_id": intake.id})
order.action_confirm()
project = order.codestra_project_id
order.action_confirm()
assert order.codestra_project_id == project
assert env["project.project"].search_count([("id", "=", project.id)]) == 1
passed("service_sale_project_handoff_idempotency")

# Senior recommendation cannot imply communication authorization.
senior_partner = env["res.partner"].create({"name": "SYNTHETIC Senior"})
profile = env["codestra.senior.profile"].create({
    "partner_id": senior_partner.id, "business_unit_id": unit_senior.id,
})
assert profile.consent_state == "unknown" and profile.dnc_state == "unknown"
passed("senior_customer_protection_defaults")

# Pipeline policy blocks skipped stages and cross-vertical stages.
stage0 = env.ref("codestra_vertical_core.stage_transportation_00")
stage1 = env.ref("codestra_vertical_core.stage_transportation_01")
stage2 = env.ref("codestra_vertical_core.stage_transportation_02")
pipeline_lead = env["crm.lead"].create({
    "name": "SYNTHETIC Pipeline Lead", "business_unit_id": unit_transport.id,
    "codestra_vertical": "transportation", "stage_id": stage0.id,
})
pipeline_lead.stage_id = stage1
try:
    pipeline_lead.stage_id = stage2.browse(env.ref("codestra_vertical_core.stage_services_00").id)
    raise AssertionError("cross-vertical transition succeeded")
except ValidationError:
    pass
passed("campaign_specific_pipeline_transition_policy")

# Inactive integration contract is idempotent and cannot deliver.
event_vals = {
    "event_id": "synthetic-event-1", "event_type": "lead.create",
    "event_timestamp": "2026-07-26 01:00:00", "record_model": "crm.lead",
    "record_reference": "SYNTHETIC", "audit_reference": "SYNTHETIC-AUDIT",
    "business_unit_id": unit_shared.id, "idempotency_key": "-".join(("fixture", "idempotency", "value")),
}
event = env["codestra.integration.event"].create(event_vals)
assert env["codestra.integration.event"].create(dict(event_vals)) == event
assert not event.delivery_enabled and event.state == "inactive"
try:
    event.delivery_enabled = True
    raise AssertionError("delivery was enabled")
except ValidationError:
    pass
passed("integration_contract_idempotency_and_delivery_lock")

# Server-side record rules protect direct search/export across business units.
agent_group = env.ref("codestra_vertical_core.group_vertical_agent")
agent = env["res.users"].create({
    "name": "SYNTHETIC Scoped Agent", "login": "synthetic-scoped-agent@example.invalid",
    "group_ids": [(6, 0, agent_group.ids)],
    "call_center_business_unit_ids": [(6, 0, unit_digital.ids)],
    "call_center_default_business_unit_id": unit_digital.id,
})
assert not env["codestra.transport.lane"].with_user(agent).search([("id", "=", lane.id)])
try:
    exported = env["codestra.transport.lane"].with_user(agent).export_data(["name"])["datas"]
    assert exported == []
except (AccessError, UserError):
    pass
passed("direct_rpc_search_and_export_scope")

for key in (
    "SEND_EVENTS", "PRODUCTION_CALLBACKS_ENABLED", "VICIDIAL_WRITES_ENABLED",
    "EXTERNAL_DIAL_ENABLED", "TRANSFERS_ENABLED", "N8N_PRODUCTION_WORKFLOWS_ENABLED",
    "ENABLE_EXTERNAL_DELIVERY", "ALLOW_LIVE_CALLS", "ALLOW_LIVE_EMAIL",
    "ALLOW_LIVE_SMS", "ALLOW_CAMPAIGN_ACTIVATION",
):
    assert env["ir.config_parameter"].sudo().get_param(key, "false").lower() == "false"
passed("all_external_delivery_flags_false")

print("\n".join(results))
print(f"PASS|acceptance_count={len(results)}")
env.cr.rollback()
print("PASS|synthetic_transaction_rolled_back")
