#!/bin/bash
# Fired by systemd (OnFailure=) when algo-trading.service exits non-zero /
# is restarted after a crash. Posts a Telegram alert using the bot creds
# already in /opt/algo-trading/.env. Install: see deploy/README.md.
set -euo pipefail

ENV_FILE="/opt/algo-trading/.env"
BOT_TOKEN=$(grep -oP '^TELEGRAM_BOT_TOKEN=\K.*' "$ENV_FILE" 2>/dev/null || true)
CHAT_ID=$(grep -oP '^TELEGRAM_CHAT_ID=\K.*' "$ENV_FILE" 2>/dev/null || true)

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
  echo "notify_service_failure.sh: missing TELEGRAM_BOT_TOKEN/CHAT_ID, skipping alert" >&2
  exit 0
fi

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d chat_id="${CHAT_ID}" \
  -d text="⚠️ [SERVICE] algo-trading.service crashed/restarted on $(hostname) at $(date -u +'%Y-%m-%d %H:%M:%S UTC')" \
  > /dev/null || true
