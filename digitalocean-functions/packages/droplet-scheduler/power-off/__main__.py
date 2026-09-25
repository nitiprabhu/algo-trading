"""
DigitalOcean Serverless Function: Power Off Droplet
Scheduled trigger: Mon-Fri at 10:30 UTC (04:00 PM IST)
"""

import json
import os
import urllib.error
import urllib.request


def send_telegram(bot_token: str, chat_id: str, text: str):
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


def main(args):
    token = args.get("DO_API_TOKEN") or os.environ.get("DO_API_TOKEN")
    droplet_id = args.get("DROPLET_ID") or os.environ.get("DROPLET_ID") or "586070363"
    tg_token = args.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = args.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")

    if not token:
        msg = "🚨 *[DO Functions] Missing `DO_API_TOKEN`.* Cannot power off droplet."
        send_telegram(tg_token, tg_chat_id, msg)
        return {"statusCode": 400, "body": {"error": "Missing DO_API_TOKEN"}}

    url = f"https://api.digitalocean.com/v2/droplets/{droplet_id}/actions"
    payload = json.dumps({"type": "power_off"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status_code = resp.status
            body = json.loads(resp.read().decode("utf-8"))
            action_status = body.get("action", {}).get("status", "in-progress")
            msg = (
                f"🔴 *[DO Functions] Droplet Power OFF Triggered*\n\n"
                f"• *Droplet ID:* `{droplet_id}`\n"
                f"• *Action Status:* `{action_status}`\n"
                f"• *Schedule:* Market Post-Close (04:00 PM IST)\n"
                f"• *Trigger:* DigitalOcean Scheduled Serverless Function"
            )
            send_telegram(tg_token, tg_chat_id, msg)
            return {"statusCode": status_code, "body": body}

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
            err_msg = err_json.get("message", err_body)
        except Exception:
            err_msg = err_body

        if "already" in err_msg.lower() and "off" in err_msg.lower():
            msg = (
                f"ℹ️ *[DO Functions] Droplet Already Powered Off*\n\n"
                f"• *Droplet ID:* `{droplet_id}`\n"
                f"• *Status:* Droplet is already powered OFF."
            )
            send_telegram(tg_token, tg_chat_id, msg)
            return {"statusCode": 200, "body": {"message": "Droplet already powered off"}}

        msg = (
            f"⚠️ *[DO Functions] Power OFF Failed!*\n\n"
            f"• *Droplet ID:* `{droplet_id}`\n"
            f"• *HTTP Status:* `{e.code}`\n"
            f"• *Error:* `{err_msg}`\n\n"
            f"⚠️ Droplet may still be running and incurring costs."
        )
        send_telegram(tg_token, tg_chat_id, msg)
        return {"statusCode": e.code, "body": {"error": err_msg}}

    except Exception as e:
        msg = (
            f"⚠️ *[DO Functions] Unexpected Error during Power OFF*\n\n"
            f"• *Droplet ID:* `{droplet_id}`\n"
            f"• *Error:* `{str(e)}`"
        )
        send_telegram(tg_token, tg_chat_id, msg)
        return {"statusCode": 500, "body": {"error": str(e)}}
