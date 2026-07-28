#!/bin/bash
# Fired by systemd (ExecStopPost=) every time algo-trading.service stops,
# for any reason: deploy restart, manual `systemctl stop`, crash, or the
# whole machine shutting down (shutdown always stops services first, so
# this also covers the droplet powering off -- including the app's own
# self-shutdown loop, the "Power Off Droplet" GH Actions workflow, or a
# manual power-off from the DO dashboard). Complements notify_service_failure.sh
# (OnFailure=, crash-only) -- expect both to fire on a real crash, only this
# one on a clean stop. Install: see deploy/README.md.
set -euo pipefail

ENV_FILE="/opt/algo-trading/.env"
BOT_TOKEN=$(grep -oP '^TELEGRAM_BOT_TOKEN=\K.*' "$ENV_FILE" 2>/dev/null || true)
CHAT_ID=$(grep -oP '^TELEGRAM_CHAT_ID=\K.*' "$ENV_FILE" 2>/dev/null || true)

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
  echo "notify_service_stop.sh: missing TELEGRAM_BOT_TOKEN/CHAT_ID, skipping alert" >&2
  exit 0
fi

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d chat_id="${CHAT_ID}" \
  -d text="🛑 [SERVICE] algo-trading.service stopped on $(hostname) at $(date -u +'%Y-%m-%d %H:%M:%S UTC') (result=${SERVICE_RESULT:-unknown})" \
  > /dev/null || true
