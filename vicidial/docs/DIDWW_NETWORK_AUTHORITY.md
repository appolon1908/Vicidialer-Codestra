# DIDWW network authority

Retrieved: 2026-08-14. Source: official DIDWW documentation.

The live Asterisk configuration identifies an outbound credential-authenticated trunk using `out.didww.com`; `sip show registry` reports no registrations. Runtime secrets are not represented here.

Official outbound signaling endpoints are documented at <https://doc.didww.com/voice/outbound-trunks/technical-data/sip-details.html>. Official inbound static-SIP signaling and RTP sources are documented at <https://doc.didww.com/voice/inbound-trunks/technical-data/sip.html>.

Authoritative IPv4 RTP source ranges:

- `46.19.208.0/21`
- `185.238.172.0/22`

The account/contract must still establish whether inbound static SIP delivery is enabled and which POPs apply before a restrictive inbound signaling ACL is deployed. Remote VICIphone/WebRTC sources are a separate trust class and must not be conflated with carrier signaling ranges.

Firewall changes remain blocked until the approved DIDWW account configuration and legitimate remote-agent source policy are reconciled. This document removes reliance on community lists; it does not invent account-specific settings.
