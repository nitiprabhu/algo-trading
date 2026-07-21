# Observability deploy steps (VPS-side, one-time)

Run once next time the droplet is up:

```bash
# 1. Log rotation
sudo cp deploy/algo-trading.logrotate /etc/logrotate.d/algo-trading

# 2. Crash alert
sudo mkdir -p /etc/systemd/system/algo-trading.service.d
sudo cp deploy/algo-trading-override.conf /etc/systemd/system/algo-trading.service.d/override.conf
sudo cp deploy/notify-service-failure.service /etc/systemd/system/notify-service-failure.service
sudo mkdir -p /opt/algo-trading/deploy
sudo cp deploy/notify_service_failure.sh /opt/algo-trading/deploy/notify_service_failure.sh
sudo chmod +x /opt/algo-trading/deploy/notify_service_failure.sh
sudo systemctl daemon-reload
```

Everything else (order-event logging, feed-health watchdog, daily digest,
1h token recheck) is in the app code and takes effect on the next
`git pull && systemctl restart algo-trading`.

## Still needs GitHub secrets (not set by this change)

To activate external monitoring (workflow-disabled detection, `/health`
uptime pings, DO API failure alerts) add these repo secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Then the `watchdog.yml` workflow (see `.github/workflows/watchdog.yml`)
will alert on: any of the 3 scheduled workflows going `disabled_manually`,
`/health` unreachable, or `do-power-on`/`do-power-off` failing.
