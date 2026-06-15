"""
scripts/refresh_token.py
────────────────────────
Daily INDstocks Flash API token renewal.

Flow:
  1. Opens headless Chromium → navigates to INDstocks login
  2. Enters INDSTOCKS_MOBILE from .env
  3. Requests OTP → sends Telegram alert: "Reply with OTP"
  4. Polls your Telegram chat (60 s timeout) for the OTP reply
  5. Submits OTP → enters INDSTOCKS_PIN from .env
  6. Goes to /app/api-trading/access-tokens → clicks "New Token"
  7. Copies the generated token
  8. Writes INDMONEY_TOKEN=<new_token> back to .env
  9. Sends Telegram confirmation: "✅ Token refreshed!"

Required .env vars:
  INDSTOCKS_MOBILE   - Your registered mobile number (digits only)
  INDSTOCKS_PIN      - Your 4- or 6-digit trading PIN
  TELEGRAM_BOT_TOKEN - Already present
  (TELEGRAM_CHAT_ID is auto-loaded from .telegram_chat_id)

Usage:
  python scripts/refresh_token.py
  python scripts/refresh_token.py --dry-run   # logs token without writing .env
"""

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Optional Playwright import (gives a clear error if not installed) ──────────
try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except ImportError:
    print("❌  Playwright is not installed. Run:\n"
          "       pip install playwright\n"
          "       playwright install chromium")
    sys.exit(1)

# ── Load .env from project root ───────────────────────────────────────────────
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"
CHAT_ID_FILE = ROOT / ".telegram_chat_id"
IST = ZoneInfo("Asia/Kolkata")

load_dotenv(dotenv_path=ENV_FILE, override=True)

INDSTOCKS_URL = "https://www.indstocks.com"
DRY_RUN = "--dry-run" in sys.argv


# ── Telegram helpers (synchronous, no asyncio dependency needed here) ─────────

def _tg_request(path: str, payload: dict) -> dict:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return {}
    url = f"https://api.telegram.org/bot{bot_token}/{path}"
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[Telegram] Request failed: {e}")
        return {}


def _tg_get(path: str, params: dict = {}) -> dict:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return {}
    qs = urllib.parse.urlencode(params)
    url = f"https://api.telegram.org/bot{bot_token}/{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[Telegram] GET failed: {e}")
        return {}


def get_chat_id() -> str | None:
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if chat_id:
        return chat_id
    if CHAT_ID_FILE.exists():
        return CHAT_ID_FILE.read_text().strip() or None
    return None


def tg_send(text: str) -> bool:
    chat_id = get_chat_id()
    if not chat_id:
        print(f"[Telegram] No chat ID — cannot send: {text}")
        return False
    result = _tg_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    })
    return result.get("ok", False)


def tg_wait_for_reply(prompt_update_id: int, timeout_secs: int = 120) -> str | None:
    """
    Poll Telegram updates for a plain-text message after prompt_update_id.
    Returns the reply text (stripped) or None if timed out.
    """
    chat_id = get_chat_id()
    offset = prompt_update_id + 1
    deadline = time.monotonic() + timeout_secs
    print(f"[Telegram] Waiting up to {timeout_secs}s for your reply…")

    while time.monotonic() < deadline:
        data = _tg_get("getUpdates", {"offset": offset, "timeout": 10})
        for update in data.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            if str(msg.get("chat", {}).get("id")) == str(chat_id):
                text = msg.get("text", "").strip()
                if text and not text.startswith("/"):
                    print(f"[Telegram] Received reply: {'*' * len(text)}")
                    return text
        # small pause between polls (long-poll already waited 10 s in Telegram)
        time.sleep(0.5)

    return None


def get_latest_update_id() -> int:
    """Return the current max update_id so we only listen for NEW messages."""
    data = _tg_get("getUpdates")
    results = data.get("result", [])
    if results:
        return max(r["update_id"] for r in results)
    return 0


# ── Token extraction helper ───────────────────────────────────────────────────

def write_token_to_env(token: str) -> None:
    """Update INDMONEY_TOKEN in .env without touching other vars."""
    if DRY_RUN:
        print(f"[dry-run] Would write token: {token[:20]}…")
        return
    set_key(str(ENV_FILE), "INDMONEY_TOKEN", token)
    print(f"✅  Wrote new token to {ENV_FILE}")


# ── Main Playwright automation ────────────────────────────────────────────────

async def refresh_token() -> bool:
    mobile = os.getenv("INDSTOCKS_MOBILE", "").strip()
    pin = os.getenv("INDSTOCKS_PIN", "").strip()

    if not mobile:
        tg_send("❌ *Token refresh failed*\n`INDSTOCKS_MOBILE` not set in `.env`")
        print("❌  INDSTOCKS_MOBILE is not set in .env")
        return False
    if not pin:
        tg_send("❌ *Token refresh failed*\n`INDSTOCKS_PIN` not set in `.env`")
        print("❌  INDSTOCKS_PIN is not set in .env")
        return False

    now = datetime.now(IST).strftime("%I:%M %p IST")
    tg_send(
        f"🔐 *INDstocks Token Refresh* — {now}\n\n"
        f"Starting login for `{mobile[:4]}****{mobile[-2:]}`…\n"
        f"You'll receive an OTP on your phone shortly."
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )
        page = await ctx.new_page()

        try:
            # ── Step 1: Navigate to login ─────────────────────────────────────
            print("[Browser] Navigating to INDstocks login…")
            await page.goto(f"{INDSTOCKS_URL}/app/login", wait_until="networkidle", timeout=30_000)
            await page.wait_for_timeout(2000)

            # ── Step 2: Enter mobile number ───────────────────────────────────
            print(f"[Browser] Entering mobile number…")
            # Try common selectors for mobile input
            mobile_selectors = [
                "input[type='tel']",
                "input[placeholder*='Mobile']",
                "input[placeholder*='mobile']",
                "input[placeholder*='Phone']",
                "input[name='mobile']",
                "input[id*='mobile']",
            ]
            mobile_input = None
            for sel in mobile_selectors:
                try:
                    mobile_input = page.locator(sel).first
                    await mobile_input.wait_for(state="visible", timeout=3000)
                    break
                except Exception:
                    mobile_input = None

            if mobile_input is None:
                raise RuntimeError("Could not find mobile number input field on login page")

            await mobile_input.fill(mobile)
            await page.wait_for_timeout(500)

            # Click Continue / Send OTP button
            continue_selectors = [
                "button:has-text('Continue')",
                "button:has-text('Send OTP')",
                "button:has-text('Get OTP')",
                "button[type='submit']",
            ]
            for sel in continue_selectors:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=3000)
                    await btn.click()
                    break
                except Exception:
                    pass

            await page.wait_for_timeout(3000)

            # ── Step 3: Ask for OTP via Telegram ─────────────────────────────
            anchor_id = get_latest_update_id()
            tg_send(
                f"📲 *OTP sent to* `+91 {mobile[:5]}*****`\n\n"
                f"Please reply here with the OTP you received.\n"
                f"_(You have 2 minutes)_"
            )

            otp = tg_wait_for_reply(anchor_id, timeout_secs=120)
            if not otp:
                tg_send("⏰ *Token refresh timed out* — no OTP received within 2 minutes.\nRun the script again if needed.")
                print("❌  No OTP received from Telegram within timeout")
                return False

            # Sanitise: only digits
            otp = "".join(c for c in otp if c.isdigit())
            print(f"[Browser] Got OTP ({len(otp)} digits), entering…")

            # ── Step 4: Enter OTP ─────────────────────────────────────────────
            # Handle both single OTP input and split-digit boxes
            otp_input_single = page.locator("input[type='text'][maxlength='6'], input[placeholder*='OTP'], input[name='otp']").first
            split_inputs = page.locator("input[maxlength='1']")

            try:
                count = await split_inputs.count()
            except Exception:
                count = 0

            if count >= 4:
                # Split digit boxes
                for i, digit in enumerate(otp):
                    if i >= count:
                        break
                    await split_inputs.nth(i).fill(digit)
                    await page.wait_for_timeout(100)
            else:
                try:
                    await otp_input_single.wait_for(state="visible", timeout=5000)
                    await otp_input_single.fill(otp)
                except Exception:
                    # Fallback: try keyboard input on focused element
                    await page.keyboard.type(otp, delay=80)

            await page.wait_for_timeout(1000)

            # Click Verify / Submit OTP
            verify_selectors = [
                "button:has-text('Verify')",
                "button:has-text('Submit')",
                "button:has-text('Login')",
                "button[type='submit']",
            ]
            for sel in verify_selectors:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=3000)
                    await btn.click()
                    break
                except Exception:
                    pass

            await page.wait_for_timeout(3000)

            # ── Step 5: Enter PIN ─────────────────────────────────────────────
            print("[Browser] Entering PIN…")
            pin_selectors = [
                "input[type='password']",
                "input[placeholder*='PIN']",
                "input[placeholder*='pin']",
                "input[name='pin']",
                "input[id*='pin']",
                "input[maxlength='6']",
                "input[maxlength='4']",
            ]
            pin_input = None
            for sel in pin_selectors:
                try:
                    pin_input = page.locator(sel).first
                    await pin_input.wait_for(state="visible", timeout=3000)
                    break
                except Exception:
                    pin_input = None

            # Also handle split PIN boxes
            split_pin = page.locator("input[maxlength='1']")
            try:
                split_count = await split_pin.count()
            except Exception:
                split_count = 0

            if split_count >= 4 and pin_input is None:
                for i, digit in enumerate(pin):
                    if i >= split_count:
                        break
                    await split_pin.nth(i).fill(digit)
                    await page.wait_for_timeout(80)
            elif pin_input:
                await pin_input.fill(pin)
            else:
                await page.keyboard.type(pin, delay=80)

            await page.wait_for_timeout(500)

            # Submit PIN
            for sel in ["button:has-text('Login')", "button:has-text('Submit')", "button[type='submit']"]:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=3000)
                    await btn.click()
                    break
                except Exception:
                    pass

            await page.wait_for_timeout(4000)

            # ── Step 6: Navigate to Access Tokens page ────────────────────────
            print("[Browser] Navigating to Access Tokens page…")
            await page.goto(
                f"{INDSTOCKS_URL}/app/api-trading/access-tokens",
                wait_until="networkidle",
                timeout=30_000,
            )
            await page.wait_for_timeout(2000)

            # Confirm we're logged in (redirect check)
            if "/login" in page.url or "/app/login" in page.url:
                tg_send(
                    "❌ *Token refresh failed*\n\n"
                    "Login was unsuccessful (wrong OTP or PIN?).\n"
                    "Please check and try again."
                )
                print("❌  Still on login page after authentication — check credentials")
                return False

            # ── Step 7: Click "New Token" ─────────────────────────────────────
            print("[Browser] Clicking 'New Token'…")
            new_token_btn_selectors = [
                "button:has-text('New Token')",
                "button:has-text('Generate Token')",
                "button:has-text('Create Token')",
                "a:has-text('New Token')",
            ]
            clicked = False
            for sel in new_token_btn_selectors:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=5000)
                    await btn.click()
                    clicked = True
                    break
                except Exception:
                    pass

            if not clicked:
                tg_send("❌ *Token refresh failed*\nCould not find the 'New Token' button on the page.")
                print("❌  Could not find 'New Token' button")
                return False

            await page.wait_for_timeout(3000)

            # ── Step 8: Extract the new token ─────────────────────────────────
            print("[Browser] Extracting new token…")
            token = None

            # Strategy A: Look for a visible text input / code element after clicking New Token
            token_selectors = [
                # Copy button sibling
                "[data-testid='access-token']",
                "input[value^='eyJ']",       # JWT starts with eyJ
                "code:has-text('eyJ')",
                "span:has-text('eyJ')",
                "p:has-text('eyJ')",
                "div:has-text('eyJ')",
            ]
            for sel in token_selectors:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=5000)
                    val = await el.get_attribute("value") or await el.inner_text()
                    val = val.strip()
                    if val.startswith("eyJ") and len(val) > 50:
                        token = val
                        break
                except Exception:
                    pass

            # Strategy B: Intercept the API response that contains the token
            if not token:
                # Check page source for JWT pattern
                content = await page.content()
                import re
                matches = re.findall(r'eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+', content)
                if matches:
                    # Pick the longest one (most likely the access token)
                    token = max(matches, key=len)

            if not token:
                tg_send(
                    "⚠️ *Token refresh partially failed*\n\n"
                    "Clicked 'New Token' but couldn't extract the token text.\n"
                    "Please copy it manually from the INDstocks dashboard."
                )
                print("❌  Could not extract token from page")
                return False

            # ── Step 9: Write to .env ─────────────────────────────────────────
            write_token_to_env(token)

            ts = datetime.now(IST).strftime("%I:%M %p IST")
            tg_send(
                f"✅ *INDstocks Token Refreshed!* — {ts}\n\n"
                f"Token starts with: `{token[:20]}…`\n"
                f"Expires: tomorrow 6:00 AM IST\n\n"
                f"_Server will use the new token on next restart._"
            )
            print(f"✅  Token refreshed successfully: {token[:20]}…")
            return True

        except PWTimeout as e:
            tg_send(f"❌ *Token refresh timed out*\nPlaywright timeout: {e}")
            print(f"❌  Playwright timeout: {e}")
            return False
        except Exception as e:
            tg_send(f"❌ *Token refresh error*\n`{type(e).__name__}: {e}`")
            print(f"❌  Unexpected error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            await browser.close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Starting token refresh — {datetime.now(IST).strftime('%Y-%m-%d %H:%M %Z')}")
    success = asyncio.run(refresh_token())
    sys.exit(0 if success else 1)
