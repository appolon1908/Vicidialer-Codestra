# Server B PJSIP Delivery Verification — 2026-09-12

This evidence records the live Server B observation performed after PR #54 was opened. It does not claim that the M5 browser test, Middleware teardown, or M15 call certification is complete.

## Delivery mechanism

Server B (65.21.67.207) uses static PJSIP configuration files for the verified WebRTC stanzas.

The active /etc/asterisk/pjsip.conf includes:

    #include "pjsip-vicidial.conf"
    #include "codestra-provisioned.d/*.conf"

No active realtime endpoint, auth, or aor mapping was present in the deployed Sorcery/extconfig configuration. The applied WebRTC records therefore live under the static codestra-provisioned.d/ include.

## Applied and read back

| Identity | AOR | Max contacts | Remove existing | Endpoint binding |
|---|---:|---:|---:|---|
| cod00016 | 6101 | 1 | true | endpoint 6101 -> AOR 6101, auth 6101-auth |
| appolon | 6901 | 1 | true | endpoint 6901 -> AOR 6901, auth 6901-auth |

Asterisk remained active. The change used PJSIP module reloads only; the core Asterisk daemon was not restarted. The redundant legacy wizard definition for 6101 was disabled after a backup was created at:

    /root/codestra-pjsip-m5-backups/disable-legacy-wizard-6101-20260912T035436Z

## M5 audit preparation

The supplied audit script is installed on Server B as:

    /usr/local/bin/m5_pjsip_audit.sh

It is owned by root:root, mode 0755, passes bash -n, and was verified with SHA-256:

    8b08e590edec4f8c2351f3fc0b110bf16f203691c2c15e61bc134a1da43f8c5e

The interactive trace capture and two-independent-browser registration rejection proof have not yet been run.

## Teardown boundary

The currently deployed Middleware source/runtime does not implement the proposed WEBRTC_PROVISIONING_ENABLED, KILL_SWITCH_OPEN, SERVER_B_PJSIP_CONFIG_PATH, or SERVER_B_RELOAD_CMD contract.

The deployed Middleware PJSIP and telephony provisioning flags remain false, and the PJSIP adapter returns kill_switch_closed. The proposed /etc/asterisk/pjsip_custom_teardown.conf is not included by the active Server B pjsip.conf, so writing that file alone would not change Asterisk state. No revoke_webrtc/disable_agent Middleware-to-PJSIP writer was found in the deployed source.

This record intentionally leaves the M5, teardown, and M15 gates open pending an approved delivery implementation and live evidence.
