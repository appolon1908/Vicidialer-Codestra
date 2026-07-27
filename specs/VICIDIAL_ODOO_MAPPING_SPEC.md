# VICIdial Server Layout and Cross-System Campaign Mapping

## Odoo 19 + VICIdial + Asterisk + Middleware + n8n + AI

# 1. Objective

Configure the VICIdial server so every campaign, user group, inbound group, lead list, script, disposition, callback, recording, and transfer route matches the corresponding records in:

* Odoo 19
* Codestra middleware
* n8n
* Asterisk
* AI services
* Reporting
* Appointment workflows
* IVR workflows

The platform must support eight business units:

1. Moy Logistics
2. Codestra
3. Senior Citizen Products
4. MoneyBee Business Loans
5. RLP International Real Estate
6. For the People
7. TradeX
8. Calderon Farm

VICIdial remains the telephony system of record.

Odoo remains the business system of record.

Middleware remains the only operational integration gateway.

n8n remains the asynchronous automation layer.

---

# 2. Core Server Layout

Recommended logical layout:

```text
VICIdial / Asterisk Server
│
├── SIP trunks and DIDs
├── Asterisk dialplan
├── VICIdial campaigns
├── Lead lists
├── User groups
├── Inbound groups
├── Closer groups
├── Scripts
├── Dispositions
├── Callbacks
├── Recordings
├── Agent sessions
├── Queue logs
└── Telephony reports
        │
        ▼
Server B Adapter
        │
        ▼
Codestra Middleware
        │
        ├── Odoo synchronization
        ├── n8n event delivery
        ├── AI event delivery
        ├── WebSocket screen pops
        ├── Idempotency
        ├── Audit
        └── Feature flags
```

Do not allow:

* Odoo to write directly to VICIdial tables
* n8n to write directly to VICIdial tables
* Browser clients to access VICIdial administrative APIs
* AI services to execute telephony actions directly

---

# 3. Mandatory Identifier Strategy

Every business object must use stable identifiers across systems.

## Business-Unit Codes

| Business Unit                 | Code  |
| ----------------------------- | ----- |
| Moy Logistics                 | `MOY` |
| Codestra                      | `COD` |
| Senior Citizen Products       | `SCP` |
| MoneyBee Business Loans       | `MBL` |
| RLP International Real Estate | `RLP` |
| For the People                | `FTP` |
| TradeX                        | `TRX` |
| Calderon Farm                 | `CAL` |

## Campaign Naming Pattern

Use:

```text
<BU>-<PURPOSE>-<DIRECTION>
```

Examples:

```text
MOY-CARRIER-OUT
COD-WEB-OUT
SCP-PRODUCT-IN
MBL-RENEWAL-OUT
```

## Inbound-Group Naming Pattern

Use:

```text
<BU>_<PURPOSE>_<FUNCTION>
```

Examples:

```text
MOY_CARRIER_CLOSERS
COD_WEB_SUPPORT
SCP_PRODUCT_RETENTION
```

## Lead-List Naming Pattern

Use:

```text
<BU>_<CAMPAIGN>_<SOURCE>_<SEQUENCE>
```

Example:

```text
COD_WEB_META_001
```

## VICIdial User Pattern

Use a controlled numeric or prefixed format.

Examples:

```text
MOY1001
COD1001
SCP1001
MBL1001
```

Do not use personal email addresses as VICIdial usernames.

---

# 4. Authoritative Cross-System Mapping

Create one mapping record in middleware for every campaign.

Required fields:

* Business-unit code
* Odoo business-unit ID
* Odoo CRM team ID
* Odoo campaign ID
* VICIdial campaign ID
* Default VICIdial list
* Default user group
* Inbound group
* Closer group
* Support group
* Retention group
* n8n workflow scope
* Script version
* Disposition-set version
* Recording policy
* Appointment policy
* IVR route
* Active environment
* Feature-flag state

Example:

```json
{
  "business_unit": "COD",
  "odoo_team_code": "COD_PRIMARY",
  "odoo_campaign_code": "COD-WEB-OUT",
  "vicidial_campaign_id": "COD-WEB-OUT",
  "default_list_id": "21001",
  "user_group": "COD_WEB_SDR",
  "closer_group": "COD_WEB_CLOSERS",
  "support_group": "COD_WEB_SUPPORT",
  "n8n_scope": "COD-WEB",
  "script_code": "COD_WEB_SDR_V1",
  "disposition_set": "COD_WEB_OUT_V1"
}
```

---

# 5. Campaign Inventory

# 5.1 Moy Logistics

## Outbound Campaigns

* `MOY-CARRIER-OUT`
* `MOY-SHIPPER-OUT`
* `MOY-DISPATCH-OUT`
* `MOY-PARTNER-OUT`
* `MOY-RETENTION-OUT`
* `MOY-UPSELL-OUT`
* `MOY-CALLBACK-OUT`

## Inbound Campaigns

* `MOY-CARRIER-IN`
* `MOY-SHIPPER-IN`
* `MOY-DISPATCH-IN`
* `MOY-SUPPORT-IN`
* `MOY-BILLING-IN`
* `MOY-APPOINTMENT-IN`

## User Groups

* `MOY_CARRIER_SDR`
* `MOY_SHIPPER_SDR`
* `MOY_DISPATCH_SDR`
* `MOY_CLOSERS`
* `MOY_TRANSFER`
* `MOY_SUPPORT`
* `MOY_RETENTION`
* `MOY_SUPERVISORS`
* `MOY_MANAGERS`
* `MOY_QA`

## Inbound and Closer Groups

* `MOY_CARRIER_CLOSERS`
* `MOY_SHIPPER_CLOSERS`
* `MOY_DISPATCH_CLOSERS`
* `MOY_CARRIER_SUPPORT`
* `MOY_SHIPPER_SUPPORT`
* `MOY_DISPATCH_SUPPORT`
* `MOY_RETENTION`
* `MOY_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
Moy Logistics
```

Primary Odoo pipeline:

```text
New Logistics Lead
→ Validation
→ Classification
→ Authority Review
→ AI Pre-Qualification
→ SDR Contact
→ Needs Assessment
→ Qualified
→ Quote
→ Closer Handoff
→ Contract
→ Onboarding
→ Active Account
→ Retention
```

## n8n Scope

* Carrier lead intake
* Shipper lead intake
* DOT/MC enrichment
* Insurance reminders
* Quote follow-up
* Carrier packet reminders
* Delivery exception alerts
* Retention reminders

---

# 5.2 Codestra

## Outbound Campaigns

* `COD-WEB-OUT`
* `COD-MOBILE-OUT`
* `COD-AI-OUT`
* `COD-ODOO-OUT`
* `COD-AUTOMATION-OUT`
* `COD-HOSTING-OUT`
* `COD-RENEWAL-OUT`
* `COD-UPSELL-OUT`
* `COD-CALLBACK-OUT`

## Inbound Campaigns

* `COD-WEB-IN`
* `COD-MOBILE-IN`
* `COD-AI-IN`
* `COD-ODOO-IN`
* `COD-SUPPORT-IN`
* `COD-BILLING-IN`
* `COD-APPOINTMENT-IN`

## User Groups

* `COD_WEB_SDR`
* `COD_MOBILE_SDR`
* `COD_AI_SDR`
* `COD_ODOO_SDR`
* `COD_TECH_CLOSERS`
* `COD_TRANSFER`
* `COD_SUPPORT`
* `COD_RENEWAL`
* `COD_SUPERVISORS`
* `COD_MANAGERS`
* `COD_QA`

## Inbound and Closer Groups

* `COD_WEB_CLOSERS`
* `COD_MOBILE_CLOSERS`
* `COD_AI_CLOSERS`
* `COD_ODOO_CLOSERS`
* `COD_WEB_SUPPORT`
* `COD_MOBILE_SUPPORT`
* `COD_AI_SUPPORT`
* `COD_ODOO_SUPPORT`
* `COD_TECH_DISCOVERY`
* `COD_RENEWAL`
* `COD_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
Codestra
```

Primary Odoo pipelines:

* Web Development
* Mobile Development
* AI Development
* Odoo Implementation
* Automation
* Hosting and Maintenance

## n8n Scope

* Website lead intake
* Mobile lead intake
* AI lead intake
* Odoo lead intake
* Website audit
* Discovery scheduling
* Proposal follow-up
* Contract reminders
* Deposit reminders
* Project handoff
* Maintenance renewal

---

# 5.3 Senior Citizen Products

## Outbound Campaigns

* `SCP-PRODUCT-OUT`
* `SCP-CAREGIVER-OUT`
* `SCP-REORDER-OUT`
* `SCP-RETENTION-OUT`
* `SCP-UPSELL-OUT`
* `SCP-CALLBACK-OUT`

## Inbound Campaigns

* `SCP-PRODUCT-IN`
* `SCP-ORDER-IN`
* `SCP-SUPPORT-IN`
* `SCP-WARRANTY-IN`
* `SCP-BILLING-IN`
* `SCP-CAREGIVER-IN`
* `SCP-APPOINTMENT-IN`

## User Groups

* `SCP_PRODUCT_SDR`
* `SCP_CAREGIVER`
* `SCP_CLOSERS`
* `SCP_TRANSFER`
* `SCP_SUPPORT`
* `SCP_WARRANTY`
* `SCP_RETENTION`
* `SCP_SUPERVISORS`
* `SCP_COMPLIANCE`
* `SCP_QA`

## Inbound and Closer Groups

* `SCP_PRODUCT_CLOSERS`
* `SCP_CAREGIVER_SUPPORT`
* `SCP_ORDER_SUPPORT`
* `SCP_PRODUCT_SUPPORT`
* `SCP_WARRANTY_SUPPORT`
* `SCP_BILLING_SUPPORT`
* `SCP_RETENTION`
* `SCP_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
Senior Citizen Products
```

Pipeline:

```text
New Inquiry
→ Consent Validation
→ First Contact
→ Customer/Caregiver Identification
→ Needs Assessment
→ Product Recommended
→ Understanding Confirmed
→ Order Preparation
→ Payment
→ Fulfillment
→ Delivery
→ Follow-Up
→ Reorder
→ Retention
```

## n8n Scope

* Product inquiry intake
* Caregiver inquiry intake
* Order confirmation
* Shipment notification
* Warranty follow-up
* Reorder reminders
* Subscription renewal
* Compliance alerts

---

# 5.4 MoneyBee Business Loans

## Outbound Campaigns

* `MBL-NEW-LOAN-OUT`
* `MBL-DOCUMENTS-OUT`
* `MBL-OFFER-OUT`
* `MBL-FUNDING-OUT`
* `MBL-RENEWAL-OUT`
* `MBL-CALLBACK-OUT`

## Inbound Campaigns

* `MBL-LOAN-IN`
* `MBL-DOCUMENTS-IN`
* `MBL-STATUS-IN`
* `MBL-SUPPORT-IN`
* `MBL-APPOINTMENT-IN`

## User Groups

* `MBL_LOAN_SDR`
* `MBL_LOAN_CLOSERS`
* `MBL_DOCUMENTS`
* `MBL_UNDERWRITING`
* `MBL_FUNDING`
* `MBL_SUPPORT`
* `MBL_RENEWAL`
* `MBL_SUPERVISORS`
* `MBL_COMPLIANCE`

## Inbound and Closer Groups

* `MBL_LOAN_CLOSERS`
* `MBL_DOCUMENT_SUPPORT`
* `MBL_UNDERWRITING_SUPPORT`
* `MBL_FUNDING_SUPPORT`
* `MBL_RENEWALS`
* `MBL_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
MoneyBee Business Loans
```

Pipeline:

```text
New Loan Lead
→ Validation
→ Consent
→ Pre-Qualification
→ Initial Call
→ Funding Assessment
→ Document Request
→ Application
→ Underwriting
→ Offer
→ Closer
→ Contract
→ Funding
→ Renewal
```

## n8n Scope

* Lead intake
* Document reminders
* Application updates
* Offer notifications
* Contract reminders
* Funding confirmations
* Renewal eligibility

---

# 5.5 RLP International Real Estate

## Outbound Campaigns

* `RLP-BUYERS-OUT`
* `RLP-SELLERS-OUT`
* `RLP-INVESTORS-OUT`
* `RLP-RENTALS-OUT`
* `RLP-FOLLOWUP-OUT`
* `RLP-CALLBACK-OUT`

## Inbound Campaigns

* `RLP-BUYERS-IN`
* `RLP-SELLERS-IN`
* `RLP-INVESTORS-IN`
* `RLP-PROPERTY-IN`
* `RLP-APPOINTMENT-IN`

## User Groups

* `RLP_BUYER_SDR`
* `RLP_SELLER_SDR`
* `RLP_INVESTOR_SDR`
* `RLP_AGENTS`
* `RLP_CLOSERS`
* `RLP_APPOINTMENTS`
* `RLP_SUPPORT`
* `RLP_SUPERVISORS`
* `RLP_MANAGERS`

## Inbound and Closer Groups

* `RLP_BUYER_AGENTS`
* `RLP_SELLER_AGENTS`
* `RLP_INVESTMENT_AGENTS`
* `RLP_PROPERTY_SUPPORT`
* `RLP_CLOSERS`
* `RLP_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
RLP International Real Estate
```

Pipeline:

```text
New Lead
→ Classification
→ Validation
→ Qualification
→ Needs Assessment
→ Financial Review
→ Property Match
→ Appointment
→ Viewing
→ Offer
→ Negotiation
→ Contract
→ Closing
→ Follow-Up
```

## n8n Scope

* Buyer intake
* Seller intake
* Investor intake
* Viewing confirmation
* Viewing reminders
* Property-match notifications
* Offer follow-up
* Closing reminders

---

# 5.6 For the People

## Outbound Campaigns

* `FTP-DONOR-OUT`
* `FTP-SPONSOR-OUT`
* `FTP-PLEDGE-OUT`
* `FTP-RETENTION-OUT`
* `FTP-CALLBACK-OUT`

## Inbound Campaigns

* `FTP-DONATIONS-IN`
* `FTP-DONOR-SUPPORT-IN`
* `FTP-SPONSOR-IN`
* `FTP-APPOINTMENT-IN`

## User Groups

* `FTP_DONOR_AGENTS`
* `FTP_SPONSOR_AGENTS`
* `FTP_CLOSERS`
* `FTP_SUPPORT`
* `FTP_RETENTION`
* `FTP_SUPERVISORS`
* `FTP_COMPLIANCE`

## Inbound and Closer Groups

* `FTP_DONATION_AGENTS`
* `FTP_SPONSORSHIP_AGENTS`
* `FTP_DONOR_SUPPORT`
* `FTP_RETENTION`
* `FTP_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
For the People
```

Pipeline:

```text
New Donor Lead
→ Consent
→ Classification
→ Pre-Qualification
→ Contact
→ Campaign Presentation
→ Donation Interest
→ Pledge
→ Payment
→ Receipt
→ Onboarding
→ Retention
→ Sponsorship
```

## n8n Scope

* Donor intake
* Pledge confirmation
* Donation reminders
* Receipt delivery
* Thank-you messages
* Recurring donation reminders
* Donor retention

---

# 5.7 TradeX

## Outbound Campaigns

* `TRX-SALES-OUT`
* `TRX-DEMO-OUT`
* `TRX-TRIAL-OUT`
* `TRX-ONBOARDING-OUT`
* `TRX-RENEWAL-OUT`
* `TRX-CALLBACK-OUT`

## Inbound Campaigns

* `TRX-SALES-IN`
* `TRX-DEMO-IN`
* `TRX-SUPPORT-IN`
* `TRX-BILLING-IN`
* `TRX-APPOINTMENT-IN`

## User Groups

* `TRX_SALES_SDR`
* `TRX_TECH_CLOSERS`
* `TRX_DEMO`
* `TRX_ONBOARDING`
* `TRX_SUPPORT`
* `TRX_RENEWAL`
* `TRX_SUPERVISORS`
* `TRX_COMPLIANCE`

## Inbound and Closer Groups

* `TRX_SALES_CLOSERS`
* `TRX_TECHNICAL_CLOSERS`
* `TRX_DEMO_SUPPORT`
* `TRX_ONBOARDING`
* `TRX_SUPPORT`
* `TRX_RENEWALS`
* `TRX_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
TradeX
```

Pipeline:

```text
New Lead
→ Validation
→ Suitability Review
→ Pre-Qualification
→ Initial Contact
→ Product Interest
→ Demo
→ Technical Discovery
→ Trial
→ Closer
→ Proposal
→ Contract
→ Payment
→ Onboarding
→ Activation
→ Renewal
```

## n8n Scope

* Lead intake
* Demo reminders
* Trial notifications
* Risk-disclosure reminders
* Proposal follow-up
* Onboarding
* Renewal reminders

---

# 5.8 Calderon Farm

## Outbound Campaigns

* `CAL-WHOLESALE-OUT`
* `CAL-EXPORT-OUT`
* `CAL-PARTNERS-OUT`
* `CAL-REORDER-OUT`
* `CAL-CALLBACK-OUT`

## Inbound Campaigns

* `CAL-BUYERS-IN`
* `CAL-EXPORT-IN`
* `CAL-SUPPORT-IN`
* `CAL-APPOINTMENT-IN`

## User Groups

* `CAL_WHOLESALE_SALES`
* `CAL_EXPORT_SALES`
* `CAL_PARTNERS`
* `CAL_LOGISTICS`
* `CAL_SUPPORT`
* `CAL_ACCOUNT_MANAGEMENT`
* `CAL_SUPERVISORS`

## Inbound and Closer Groups

* `CAL_WHOLESALE_CLOSERS`
* `CAL_EXPORT_CLOSERS`
* `CAL_BUYER_SUPPORT`
* `CAL_LOGISTICS`
* `CAL_ACCOUNT_MANAGEMENT`
* `CAL_SUPERVISORS`

## Odoo Mapping

CRM team:

```text
Calderon Farm
```

Pipeline:

```text
New Buyer
→ Classification
→ Validation
→ Product Interest
→ Quantity Assessment
→ Packaging Requirements
→ Price Request
→ Sample or Inspection
→ Quotation
→ Negotiation
→ Purchase Order
→ Harvest Allocation
→ Packing
→ Delivery
→ Invoice
→ Payment
→ Repeat Order
```

## n8n Scope

* Wholesale lead intake
* Export lead intake
* Product availability
* Sample requests
* Inspection reminders
* Quotation follow-up
* Harvest notifications
* Delivery notifications
* Repeat-order reminders

---

# 6. Lead-List Design

Every outbound campaign must have dedicated lists.

Example ranges:

| Business Unit     | Suggested List Range |
| ----------------- | -------------------: |
| Moy Logistics     |        `11000–11999` |
| Codestra          |        `21000–21999` |
| Senior Products   |        `31000–31999` |
| MoneyBee          |        `41000–41999` |
| RLP International |        `51000–51999` |
| For the People    |        `61000–61999` |
| TradeX            |        `71000–71999` |
| Calderon Farm     |        `81000–81999` |
| Staging/Test      |        `91000–91999` |

Example:

```text
21001 COD_WEB_META
21002 COD_WEB_GOOGLE
21003 COD_WEB_VENDOR
21004 COD_WEB_CALLBACK
```

List rules:

* One business unit only
* One campaign family only
* Source identified
* Environment identified
* Default status defined
* Calling hours defined
* Time-zone handling defined
* Consent policy defined
* Retention and purge policy defined

---

# 7. Script Layout

Every campaign must have its own script.

Required script components:

1. Opening
2. Company identification
3. Agent identification
4. Reason for call
5. Recording disclosure
6. Consent confirmation
7. Qualification
8. Discovery
9. Product or service explanation
10. Objection handling
11. Pricing or commercial terms
12. Transfer introduction
13. Closing
14. Required disclosures
15. Callback handling
16. Do-not-call handling

Script naming:

```text
<BU>_<CAMPAIGN>_<ROLE>_V<VERSION>
```

Example:

```text
COD_WEB_SDR_V1
SCP_PRODUCT_CLOSER_V2
MBL_LOAN_DOCUMENTS_V1
```

Odoo and VICIdial must use the same script code and version.

---

# 8. Disposition Layout

Create universal and campaign-specific statuses.

## Universal Statuses

* `NA` — No Answer
* `BUSY` — Busy
* `VM` — Voicemail
* `WN` — Wrong Number
* `DC` — Disconnected
* `CB` — Callback
* `NI` — Not Interested
* `DNC` — Do Not Call
* `QUAL` — Qualified
* `NQUAL` — Not Qualified
* `XFER` — Transferred
* `XFAIL` — Transfer Failed
* `APPT` — Appointment Scheduled
* `SALE` — Sale Completed
* `SUP` — Support Required
* `ESC` — Escalated
* `DUP` — Duplicate
* `EXIST` — Existing Customer

## Campaign-Specific Examples

### Moy Logistics

* `DOTREV`
* `QUOTE`
* `CARRIERPKT`
* `CONTRACT`

### Codestra

* `DISCOVERY`
* `TECHREV`
* `PROPOSAL`
* `DEPOSIT`

### Senior Products

* `PRODINFO`
* `ORDERPREP`
* `PAYPEND`
* `REORDER`

### MoneyBee

* `DOCREQ`
* `APPSTART`
* `UWREV`
* `OFFER`
* `FUNDED`

Each status must map to:

* Odoo pipeline stage
* Required fields
* Follow-up activity
* Callback behavior
* Suppression behavior
* n8n workflow trigger
* Reporting category

---

# 9. Appointment and Callback Layout

Callbacks must remain tied to:

* Business unit
* Campaign
* VICIdial lead ID
* Odoo lead ID
* Agent
* Customer
* Time zone
* Scheduled time
* Callback reason
* Appointment ID

Flow:

```text
Odoo appointment
→ Middleware validation
→ VICIdial callback publication
→ Read-back
→ Reminder schedule
→ APPT_PREP
→ Agent callback
→ Disposition
→ Odoo completion
```

n8n handles:

* Email reminders
* SMS reminders
* Calendar synchronization

Middleware handles:

* VICIdial callback creation
* APPT_PREP
* Start-call validation
* Resume

---

# 10. Same-Campaign Transfer Layout

Each campaign must have dedicated transfer groups.

Example:

```text
COD-WEB-OUT
→ COD_WEB_CLOSERS
→ COD_WEB_SUPPORT
→ COD_WEB_SUPERVISORS
```

Blocked:

```text
COD-WEB-OUT
→ COD_AI_CLOSERS
```

Required transfer data:

* Source campaign
* Source agent
* Customer
* Lead
* Qualification summary
* Need
* Budget or value
* Timeline
* Objection
* Compliance state
* Target group
* Transfer result

The middleware must validate the destination against the authoritative mapping.

---

# 11. IVR Layout

Recommended DID groups:

| DID Type           | Route                  |
| ------------------ | ---------------------- |
| Main company DID   | Main business-unit IVR |
| Moy Logistics DID  | Moy Logistics IVR      |
| Codestra DID       | Codestra IVR           |
| SCP DID            | Senior Products IVR    |
| MoneyBee DID       | MoneyBee IVR           |
| RLP DID            | RLP IVR                |
| For the People DID | Fundraising IVR        |
| TradeX DID         | TradeX IVR             |
| Calderon Farm DID  | Farm IVR               |
| Appointment DID    | Appointment lookup     |
| Support DID        | Support selection      |

IVR routes must end in campaign-specific inbound groups.

Every IVR session must pass to middleware:

* Source DID
* Call UniqueID
* Business unit
* Selected campaign
* Language
* Menu path
* Customer intent
* Queue
* Priority
* Callback request

---

# 12. Agent Profile Synchronization

Odoo is authoritative for the desired agent profile.

Fields:

* Employee
* Odoo user
* Business unit
* Department
* Team
* Supervisor
* Role
* VICIdial user
* User group
* Campaigns
* Inbound groups
* Closer groups
* Languages
* Skills
* Active status
* WebRTC endpoint

Sync flow:

```text
Odoo employee updated
→ Middleware desired-state calculation
→ Server B adapter
→ VICIdial user/group update
→ Read-back
→ Odoo sync status
```

Do not delete historical users.

Deactivate and preserve reporting history.

---

# 13. Middleware API Requirements

Required campaign endpoints:

```text
GET  /api/v1/telephony/campaigns
GET  /api/v1/telephony/campaigns/{id}
POST /api/v1/telephony/campaigns/validate
POST /api/v1/telephony/campaigns/sync
```

Lead endpoints:

```text
POST /api/v1/telephony/leads/publish
POST /api/v1/telephony/leads/update
GET  /api/v1/telephony/leads/{id}
```

Agent endpoints:

```text
POST /api/v1/telephony/agents/sync
POST /api/v1/telephony/agents/disable
POST /api/v1/telephony/agents/logout
GET  /api/v1/telephony/agents/{id}
```

Call endpoints:

```text
POST /api/v1/telephony/calls/dial
POST /api/v1/telephony/calls/hangup
POST /api/v1/telephony/calls/hold
POST /api/v1/telephony/calls/resume
POST /api/v1/telephony/calls/disposition
```

Transfer endpoints:

```text
POST /api/v1/telephony/transfers/validate
POST /api/v1/telephony/transfers/start
POST /api/v1/telephony/transfers/accept
POST /api/v1/telephony/transfers/reject
POST /api/v1/telephony/transfers/complete
```

Appointment endpoints:

```text
POST /api/v1/appointments/{id}/pause-agent
POST /api/v1/appointments/{id}/start
POST /api/v1/appointments/{id}/resume-agent
```

IVR endpoints:

```text
POST /api/v1/ivr/sessions
POST /api/v1/ivr/selections
POST /api/v1/ivr/routes
POST /api/v1/ivr/callbacks
```

---

# 14. n8n Matching Requirements

Every n8n workflow must use the exact same:

* Business-unit code
* Campaign ID
* Odoo campaign code
* Middleware mapping
* Event schema
* Callback type
* Disposition code
* Template code

Example:

```text
STG-COD-LEAD-META-INGEST-V1
```

Must submit:

```json
{
  "business_unit": "COD",
  "campaign_id": "COD-WEB-OUT"
}
```

Middleware must reject:

```json
{
  "business_unit": "COD",
  "campaign_id": "SCP-PRODUCT-OUT"
}
```

---

# 15. Reporting Synchronization

VICIdial must expose:

* Call records
* Agent sessions
* Queue events
* Pause events
* Dispositions
* Transfers
* Campaign statistics
* Recording references

Middleware must normalize these into Odoo reporting records.

Required keys:

* Call UniqueID
* VICIdial lead ID
* Agent
* Campaign
* Inbound group
* Start time
* Answer time
* End time
* Talk time
* Queue time
* Hold time
* Wrap-up
* Disposition
* Transfer result
* Recording reference

---

# 16. Server Directory Layout

Recommended Server B structure:

```text
/opt/codestra-vicidial/
├── adapters/
│   ├── reporting/
│   ├── ivr/
│   ├── callbacks/
│   ├── appointments/
│   ├── provisioning/
│   └── agent-sync/
├── config/
│   ├── campaigns/
│   ├── inbound-groups/
│   ├── user-groups/
│   ├── dispositions/
│   ├── scripts/
│   ├── did-mappings/
│   └── feature-flags/
├── schemas/
├── migrations/
├── scripts/
├── tests/
├── evidence/
├── backups/
└── README.md
```

Do not store credentials in these repositories.

Use protected secret references.

---

# 17. Environment Separation

Create separate identifiers for:

* Development
* Staging
* Production

Staging examples:

* `MOY-TEST-OUT`
* `COD-TEST-OUT`
* `SCP-TEST-OUT`
* `MBL-TEST-OUT`
* `RLP-TEST-OUT`
* `FTP-TEST-OUT`
* `TRX-TEST-OUT`
* `CAL-TEST-OUT`

Test groups:

* `COD_TEST_CLOSERS`
* `SCP_TEST_SUPPORT`
* `MOY_TEST_SUPERVISORS`

Never use real customer lists for acceptance testing.

---

# 18. Feature Flags

Required Server B flags:

* `ENABLE_VICIDIAL_WRITES`
* `ENABLE_CAMPAIGN_SYNC`
* `ENABLE_AGENT_SYNC`
* `ENABLE_LEAD_PUBLICATION`
* `ENABLE_LIVE_CALL_CONTROL`
* `ENABLE_WARM_TRANSFER`
* `ENABLE_IVR_ROUTING`
* `ENABLE_IVR_CALLBACKS`
* `ENABLE_APPT_PREP`
* `ENABLE_PROVISIONING`
* `ENABLE_PRODUCTION_DIALING`

All must default to false in new environments.

---

# 19. Validation Tests

## Campaign Mapping Test

For every campaign:

1. Confirm Odoo campaign exists.
2. Confirm middleware mapping exists.
3. Confirm VICIdial campaign exists.
4. Confirm default list exists.
5. Confirm user group exists.
6. Confirm inbound group exists.
7. Confirm script exists.
8. Confirm dispositions exist.
9. Confirm n8n workflows reference the same campaign.
10. Confirm feature flags are false.

## Agent Sync Test

1. Create staging employee.
2. Assign business unit.
3. Assign campaign.
4. Sync through middleware.
5. Confirm VICIdial user.
6. Confirm user group.
7. Confirm inbound groups.
8. Confirm Odoo read-back.
9. Deactivate.
10. Confirm access revoked.

## Lead Publication Test

1. Create synthetic Odoo lead.
2. Assign campaign.
3. Publish through middleware.
4. Confirm VICIdial lead.
5. Confirm list.
6. Confirm campaign.
7. Retry same request.
8. Confirm no duplicate.

## Inbound Test

1. Call test DID.
2. Select IVR option.
3. Confirm campaign.
4. Confirm inbound group.
5. Confirm agent screen pop.
6. Confirm Odoo call record.

## Transfer Test

1. Start test call.
2. Transfer to same-campaign closer.
3. Confirm success.
4. Attempt cross-campaign transfer.
5. Confirm rejection.

## Appointment Test

1. Create test appointment.
2. Trigger reminder.
3. Trigger APPT_PREP.
4. Confirm agent paused.
5. Start call.
6. Complete disposition.
7. Confirm agent resumed.

---

# 20. Required Mapping Report

Codex must generate:

```text
reports/vicidial-campaign-matrix.csv
reports/vicidial-user-group-matrix.csv
reports/vicidial-inbound-group-matrix.csv
reports/vicidial-list-matrix.csv
reports/vicidial-script-matrix.csv
reports/vicidial-disposition-matrix.csv
reports/odoo-vicidial-mapping.csv
reports/n8n-vicidial-mapping.csv
reports/vicidial-test-results.md
reports/vicidial-blockers.md
reports/vicidial-production-gate.md
```

Campaign matrix columns:

* Business unit
* Odoo CRM team
* Odoo campaign
* VICIdial campaign
* Direction
* Default list
* User group
* Inbound group
* Closer group
* Script
* Disposition set
* n8n workflow scope
* Middleware mapping
* Environment
* Feature-flag state
* Implementation status
* Test status

---

# 21. Codex Implementation Instruction

Implement and validate the VICIdial server layout described in this specification.

Begin with a read-only inventory of:

* Existing VICIdial campaigns
* Lists
* User groups
* Inbound groups
* Scripts
* Dispositions
* DIDs
* IVR routes
* Agent users
* Remote agents
* Phones and endpoints
* Recording policies
* Callback settings
* Asterisk dialplan
* Middleware adapters
* Odoo campaign records
* n8n workflow campaign references
* Existing feature flags
* Existing evidence and backups

Do not create duplicate campaigns or groups when compatible records already exist.

Create one canonical mapping between:

* Odoo business unit
* Odoo CRM team
* Odoo campaign
* VICIdial campaign
* VICIdial list
* VICIdial user group
* VICIdial inbound group
* Script
* Disposition set
* n8n workflow scope
* Middleware API mapping

Use the exact business-unit codes:

* MOY
* COD
* SCP
* MBL
* RLP
* FTP
* TRX
* CAL

Implement the campaign inventories, user groups, inbound groups, and list ranges specified in this document.

Keep live transfers inside the active campaign.

Do not allow Odoo, n8n, browser clients, or AI services to write directly to VICIdial tables.

Route every operational action through authenticated middleware and the accepted Server B adapter.

Use synthetic leads, test DIDs, test users, test endpoints, and non-customer destinations.

Keep production dialing, lead publication, IVR activation, callbacks, agent synchronization, and provisioning disabled.

For every mapping, assign:

* PASS
* PARTIAL
* FAIL
* MISSING
* BLOCKED
* NOT_TESTED
* STAGING_READY
* PRODUCTION_BLOCKED

A PASS requires:

* Configuration exists
* Middleware mapping exists
* Functional test passes
* Read-back confirms the result
* Evidence is recorded

Generate all required mapping and test reports.

End with a formal NO-GO, STAGING-ONLY, CONDITIONAL-GO, or GO recommendation.

Do not enable production.

---

# 22. Definition of Done

The VICIdial layout is complete when:

* Every Odoo business unit maps to the correct VICIdial campaign family.
* Every campaign has a dedicated list strategy.
* Every campaign has authorized user groups.
* Every campaign has dedicated inbound and closer groups.
* Every campaign has versioned scripts.
* Every campaign has controlled dispositions.
* Odoo, middleware, n8n, and VICIdial use identical campaign identifiers.
* Lead publication creates one correct VICIdial lead.
* Call events synchronize back to Odoo once.
* Agent desired-state synchronization works.
* Same-campaign transfers succeed.
* Cross-campaign transfers are blocked.
* Appointment callbacks and APPT_PREP work through middleware.
* IVR routes to the correct campaign groups.
* Reporting records reconcile across systems.
* All production feature flags remain disabled.
* Test evidence exists for every passing configuration.
