#!/bin/sh
set -eu

# These names are the canonical runtime contract consumed by the adapter and
# deployment manifests. Only the exact, policy-backed single-agent profile may
# enable its two dedicated gates; every unrelated capability remains false.
profile="${CODESTRA_ADAPTER_DEPLOYMENT:-production-candidate}"
if [ "$profile" = production-agent-provisioning ]; then
  [ "${ENABLE_VICIDIAL_WRITES:-false}" = true ] || {
    echo "refusing to start: bounded profile requires ENABLE_VICIDIAL_WRITES=true" >&2
    exit 78
  }
  [ "${ENABLE_AGENT_PROVISIONING:-false}" = true ] || {
    echo "refusing to start: bounded profile requires ENABLE_AGENT_PROVISIONING=true" >&2
    exit 78
  }
  test -r "${CODESTRA_AGENT_PROVISIONING_POLICY_FILE:-}" || {
    echo "agent provisioning policy is not readable" >&2
    exit 78
  }
else
  for flag in ENABLE_VICIDIAL_WRITES ENABLE_AGENT_PROVISIONING
  do
    eval "value=\${$flag:-false}"
    [ "$value" = false ] || {
      echo "refusing to start: $flag requires production-agent-provisioning" >&2
      exit 78
    }
  done
fi

for flag in ENABLE_PROVISIONING ENABLE_AGENT_SYNC \
  ENABLE_LEAD_PUBLICATION ENABLE_LIVE_CALL_CONTROL ENABLE_WARM_TRANSFER \
  ENABLE_IVR_ROUTING ENABLE_IVR_CALLBACKS ENABLE_PRODUCTION_DIALING \
  ENABLE_SMS_DELIVERY ENABLE_EMAIL_DELIVERY ENABLE_EXTERNAL_DELIVERY ENABLE_AI_ACTIONS
do
  eval "value=\${$flag:-false}"
  [ "$value" = false ] || {
    echo "refusing to start: $flag must remain false" >&2
    exit 78
  }
done

# Stale aliases are rejected rather than silently becoming ineffective safety
# controls. They may be removed from external deployment tooling only after all
# callers have moved to the canonical names above.
for legacy in ENABLE_CALLBACK_WRITES ENABLE_TRANSFER_POLICY_SYNC ENABLE_PSTN_DIALING
do
  eval "value=\${$legacy+x}"
  [ -z "$value" ] || {
    echo "refusing to start: deprecated feature flag $legacy is not supported" >&2
    exit 78
  }
done

test -r "${CODESTRA_ADAPTER_HMAC_FILE:-/run/secrets/adapter-hmac}" || {
  echo "adapter HMAC secret is not readable" >&2
  exit 78
}

python - "$CODESTRA_RELEASE_ROOT" <<'PY'
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
sha = (root / "SOURCE.COMMIT").read_text().strip()
build = json.loads((root / "BUILD_INFO.json").read_text())
manifest = json.loads((root / "RELEASE_MANIFEST.json").read_text())
if root.name != sha or build.get("git_sha") != sha or manifest.get("source_sha") != sha:
    raise SystemExit("release tuple mismatch")
if os.environ.get("SOURCE_SHA") != sha:
    raise SystemExit("container revision mismatch")
PY

exec "$@"
