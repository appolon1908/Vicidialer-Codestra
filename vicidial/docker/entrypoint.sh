#!/bin/sh
set -eu

for flag in ENABLE_VICIDIAL_WRITES ENABLE_PROVISIONING ENABLE_AGENT_SYNC \
  ENABLE_LEAD_PUBLICATION ENABLE_CALLBACK_WRITES ENABLE_TRANSFER_POLICY_SYNC \
  ENABLE_PSTN_DIALING ENABLE_SMS_DELIVERY ENABLE_EMAIL_DELIVERY ENABLE_EXTERNAL_DELIVERY
do
  eval "value=\${$flag:-false}"
  [ "$value" = false ] || {
    echo "refusing to start: $flag must remain false" >&2
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
