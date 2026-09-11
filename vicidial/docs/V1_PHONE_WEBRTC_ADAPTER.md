# V1 VICIdial phone and WebRTC adapter

This contract is limited to the staging synthetic lane.

## Authorities

- Middleware owns the ordinary extension allocation ledger.
- The VICIdial adapter performs the bounded VICIdial phone/agent read-back and
  keeps a local idempotency/reservation ledger.
- The PJSIP/Asterisk authority issues the expiring credential. The adapter
  stores only credential and ticket hashes.
- The WebSocket gateway owns the browser connection and its durable
  single-device guard.

The VICIdial adapter must not allocate an extension independently of a
Middleware allocation reference. A generic reservation is a secondary
VICIdial-side collision check, not a replacement for Middleware allocation.

## Approved V1 scope

Every V1 phone command is rejected unless:

- environment=STAGING;
- context.campaign_id=TEST_SYN;
- context.business_unit=TEST;
- outbound destination scope is exactly ["6000"];
- PSTN and production-dialing gates are closed.

6101 is not eligible for generic allocation. It has one explicit adoption
contract:

- employee: COD.2026.00016;
- VICIdial user: cod00016;
- campaign: TEST_SYN;
- allocation reference: STATIC-TEST_SYN-6101;
- the submitted existing_state_sha256 must match the allowlisted, redacted
  VICIdial phone read-back.

Adoption never deletes, renames, or reallocates the existing phone row.

## Adapter routes

All protected routes use the existing signed v2 request headers and a distinct
capability:

- POST /v1/extensions/reserve — telephony:extension-reserve;
- POST /v1/extensions/adopt — telephony:extension-adopt;
- GET /v1/extensions/{extension}/actual-state — telephony:read;
- POST /v1/webrtc/credentials — telephony:webrtc-provision;
- POST /v1/webrtc/revoke — telephony:webrtc-revoke.

/v1/agents/provision-disabled remains the separate, exact synthetic
break-glass profile and is not used by this lifecycle.

## WebRTC lifecycle

1. Adopt or reserve the extension.
2. Synchronize the disabled VICIdial user and campaign membership.
3. Read back the phone row and disabled agent.
4. Request a credential from the local authority with a five-minute expiry.
5. Return the credential only over the protected internal boundary; never log or
   persist it.
6. Persist a one-use ticket hash and credential hash.
7. Reject a second active session with HTTP 409 and code
   WEBRTC_SESSION_ALREADY_ACTIVE.
8. On disable/suspend, invalidate the ticket/session ledger and have Middleware
   revoke PJSIP contacts. A disabled VICIdial row alone is not a successful
   disable until the PJSIP read-back confirms zero active contacts.

A successful adapter response does not by itself enable PSTN. The browser
facade and the WebSocket gateway must independently enforce the same synthetic
contract.

## Read-back

actual-state reports:

- the redacted VICIdial phones record;
- the redacted VICIdial user record;
- reservation ownership and state;
- phone, disabled-agent, and extension-binding checks;
- a PJSIP check marked delegated until Middleware verifies the endpoint.

Middleware may transition codestra.agent.channel from PARTIAL to EFFECTIVE
only after the VICIdial checks and PJSIP endpoint/contact checks all pass.

## Safe activation sequence

The feature flags are default-deny. A staging rehearsal must prove:

- registration succeeds with the expiring credential;
- a second browser session is rejected;
- 6101 -> 6000 reaches the synthetic echo lane;
- call.ringing, call.answered, and teardown events are observed;
- media is established and released;
- disable revokes the session/ticket and leaves no PJSIP contact.

No live/PSTN route is part of V1.
