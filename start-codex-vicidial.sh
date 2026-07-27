#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${1:-/root/codestra-vicidial-completion}"

test -d "$REPO" || { echo "Missing repository: $REPO" >&2; exit 1; }
test -f "$REPO/AGENTS.md" || { echo "Missing AGENTS.md" >&2; exit 1; }
test -f "$REPO/CODEX_VICIDIAL_SERVER.md" || { echo "Missing CODEX_VICIDIAL_SERVER.md" >&2; exit 1; }
command -v codex >/dev/null 2>&1 || { echo "Codex CLI is not installed" >&2; exit 1; }

exec codex \
  --cd "$REPO" \
  --sandbox danger-full-access \
  --ask-for-approval never \
  "Read AGENTS.md, CODEX_VICIDIAL_SERVER.md, docs/VICIDIAL_ADAPTER_CONTRACT.md, docs/AUTOMATIC_CAMPAIGN_PROVISIONING.md, and specs/VICIDIAL_ODOO_MAPPING_SPEC.md completely. Begin with read-only VICIdial/Asterisk inventory, preserve working telephony, run current tests, then implement the restricted adapter and missing synchronization. Do not stop after producing a plan."
