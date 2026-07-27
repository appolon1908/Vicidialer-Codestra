#!/usr/bin/env bash
set -Eeuo pipefail

ZIP="${1:-/root/codestra_vicidial_server_65.21.67.207.zip}"
TARGET="/root/codestra-vicidial-completion"
STAGE="/root/.codestra-vicidial-package"

test -f "$ZIP" || { echo "Missing ZIP: $ZIP" >&2; exit 1; }

if command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive install unzip rsync
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y unzip rsync
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y unzip rsync
elif command -v yum >/dev/null 2>&1; then
  yum install -y unzip rsync
fi

rm -rf "$STAGE"
mkdir -p "$STAGE" "$TARGET"
unzip -o "$ZIP" -d "$STAGE"

PACKAGE_ROOT="$(find "$STAGE" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
test -n "$PACKAGE_ROOT"

rsync -a "$PACKAGE_ROOT/" "$TARGET/"
chmod 700 "$TARGET/start-codex-vicidial.sh"

touch "$TARGET/.gitignore"
for rule in \
  "config/vicidial-server.env" \
  ".env" \
  ".env.*" \
  "*.pem" \
  "*.key" \
  "*.p12" \
  "*.pfx" \
  "evidence/private/" \
  "reports/private/"
do
  grep -qxF "$rule" "$TARGET/.gitignore" 2>/dev/null || printf '%s\n' "$rule" >> "$TARGET/.gitignore"
done

if [[ ! -f "$TARGET/config/vicidial-server.env" ]]; then
  cp "$TARGET/config/vicidial-server.env.example" "$TARGET/config/vicidial-server.env"
fi

printf '\nVICIDIAL_SERVER_PACKAGE_INSTALLED=YES\n'
printf 'TARGET=%s\n' "$TARGET"
printf 'NEXT=Edit %s/config/vicidial-server.env, then run %s/start-codex-vicidial.sh\n' "$TARGET" "$TARGET"
