# DigitalOcean Serverless Functions — Droplet Power Scheduler

This module replaces the GitHub Actions cron workflows (`do-power-on.yml` and `do-power-off.yml`) with native **DigitalOcean Serverless Scheduled Functions**.

---

## Why Switch from GitHub Actions to DigitalOcean Functions?

| Feature | GitHub Actions Cron | DigitalOcean Serverless Functions |
| :--- | :--- | :--- |
| **Trigger Reliability** | Highly prone to queuing delays (30m – 2h lag during peak UTC hours) | **Precise, sub-second execution** at the exact scheduled minute |
| **Cost** | Free (within GH minutes) | **100% Free** (90,000 GB-sec/mo free tier; 2 runs/day uses ~0.0003%) |
| **Trading System Safety** | High risk of missing market open (9:00/9:15 AM IST) | Droplet is guaranteed to boot at **08:30 AM IST** |
| **Telegram Notifications** | Basic cURL alerts on failure only | Formatted Telegram alerts with status & error diagnostics |

---

## Scheduled Timing (IST)

* **Power ON:** `0 3 * * 1-5`
  * **Time:** 03:00 UTC = **08:30 AM IST**, Monday – Friday (Trading Days).
  * Boots the droplet 30 minutes before market pre-open (09:00 AM IST) so systemd services, tokens, and data feeds initialize smoothly.
* **Power OFF:** `30 10 * * *`
  * **Time:** 10:30 UTC = **04:00 PM IST**, Every Day (Monday – Sunday).
  * Shuts down droplet after market close and final reconciliations to save compute costs. Runs daily to ensure the server is never left running accidentally.

---

## Project Structure

```
digitalocean-functions/
├── project.yml                                    # Package, triggers & environment spec
├── .env.example                                   # Sample environment variables
├── README.md                                      # Documentation & deployment guide
└── packages/
    └── droplet-scheduler/
        ├── power-on/
        │   └── __main__.py                        # Power ON handler + Telegram alerts
        └── power-off/
            └── __main__.py                        # Power OFF handler + Telegram alerts
```

---

## Deployment Methods

### Option A: Deploy via `doctl` (Recommended)

1. **Install `doctl` and the serverless extension:**
   ```bash
   brew install doctl
   doctl serverless install
   ```

2. **Authenticate `doctl`:**
   ```bash
   doctl auth init
   # Enter your DigitalOcean Personal Access Token
   ```

3. **Connect to or create a Serverless Namespace:**
   ```bash
   doctl serverless namespaces create droplet-control --region blr1
   # Or connect to your existing namespace:
   # doctl serverless connect droplet-control
   ```

4. **Set up Environment Variables:**
   Create `digitalocean-functions/.env` (or set them via `doctl`):
   ```ini
   DO_API_TOKEN=dop_v1_your_token_here
   DROPLET_ID=586070363
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   ```

5. **Deploy the project:**
   ```bash
   cd digitalocean-functions
   doctl serverless deploy . --env .env
   ```

6. **Test the function manually:**
   ```bash
   doctl serverless functions invoke droplet-scheduler/power-on
   ```

---

### Option B: Deploy via DigitalOcean Cloud Console (UI / No CLI)

1. Open [DigitalOcean Cloud Console](https://cloud.digitalocean.com/functions).
2. Click **Functions** in the sidebar.
3. Click **Create Function**:
   * **Namespace:** Select or create a namespace (e.g. `algo-trading-schedulers`).
   * **Package:** Name it `droplet-scheduler`.
   * **Function 1:** `power-on` (Runtime: `Python 3`). Paste content from `packages/droplet-scheduler/power-on/__main__.py`.
   * **Function 2:** `power-off` (Runtime: `Python 3`). Paste content from `packages/droplet-scheduler/power-off/__main__.py`.
4. Under **Settings -> Environment Variables**, add:
   * `DO_API_TOKEN`: Your DO personal access token
   * `DROPLET_ID`: `586070363`
   * `TELEGRAM_BOT_TOKEN`: Your bot token
   * `TELEGRAM_CHAT_ID`: Your chat ID
5. Under **Triggers / Schedules**:
   * Add Scheduled Trigger for `power-on`: `0 3 * * 1-5` (08:30 AM IST, Mon-Fri).
   * Add Scheduled Trigger for `power-off`: `30 10 * * 1-5` (04:00 PM IST, Mon-Fri).

---

## DigitalOcean MCP Server Integration

To manage your Droplets, Functions, and Apps directly with AI using the official DigitalOcean Model Context Protocol (MCP) server:

1. **Add MCP Config to your IDE / MCP Client:**
   ```json
   {
     "mcpServers": {
       "digitalocean": {
         "command": "npx",
         "args": ["-y", "@digitalocean/mcp"],
         "env": {
           "DIGITALOCEAN_API_TOKEN": "dop_v1_your_token_here"
         }
       }
     }
   }
   ```

2. **Capabilities available through DO MCP:**
   * "List all my droplets and check power status"
   * "Power on droplet 586070363"
   * "List serverless functions and trigger history"
   * "Get droplet CPU/RAM metrics"

---

## Decommissioning Old GitHub Workflows

Once your DigitalOcean Functions are live:
1. In GitHub Repository -> **Actions** -> **Power On Droplet** -> Click `...` -> **Disable workflow**.
2. In GitHub Repository -> **Actions** -> **Power Off Droplet** -> Click `...` -> **Disable workflow**.
