import os
import json
import urllib.request
import urllib.parse
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

IST = ZoneInfo("Asia/Kolkata")


class TelegramNotifier:
    def __init__(self):
        # Read from environment
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "8740074056:AAFi3qKifRyMw9hsIwU1qC6Qqez2DqwA4Qo")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        # Local cache file for resolved chat ID
        self.cache_file = ".telegram_chat_id"
        if not self._chat_id and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    self._chat_id = f.read().strip()
                    print(f"DEBUG: Loaded cached Telegram Chat ID: {self._chat_id}")
            except Exception as e:
                print(f"DEBUG: Failed to read cached Chat ID: {e}")

    @property
    def chat_id(self) -> Optional[str]:
        return self._chat_id

    async def resolve_chat_id(self) -> Optional[str]:
        """Poll getUpdates to automatically find the first chat that has messaged the bot."""
        if self._chat_id:
            return self._chat_id
            
        print("DEBUG: Starting background loop to resolve Telegram Chat ID...")
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        
        while not self._chat_id:
            try:
                def fetch():
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.loads(response.read().decode())

                data = await asyncio.to_thread(fetch)
                results = data.get("result", [])
                if results:
                    # Find the latest message or group event
                    for r in reversed(results):
                        chat = None
                        
                        # 1. Try message or channel post
                        msg = r.get("message") or r.get("channel_post")
                        if msg:
                            chat = msg.get("chat", {})
                        
                        # 2. Try my_chat_member event (e.g., added to a group/channel)
                        mcm = r.get("my_chat_member")
                        if mcm and not chat:
                            chat = mcm.get("chat", {})
                            
                        if chat:
                            cid = str(chat.get("id"))
                            if cid:
                                self._chat_id = cid
                                # Cache it
                                try:
                                    with open(self.cache_file, "w") as f:
                                        f.write(cid)
                                except Exception as ce:
                                    print(f"DEBUG: Error writing chat ID cache: {ce}")
                                
                                # Send validation/greeting message
                                await self.send_message(
                                    "🤖 *ChartEdge AI Bot Initialized*\n\n"
                                    "✅ Connection successful! Real-time alerts for live trades will be posted in this group."
                                )
                                print(f"DEBUG: Successfully resolved Telegram Chat ID: {self._chat_id}")
                                return cid
            except Exception as e:
                print(f"Error resolving Telegram chat ID: {e}")
                
            # Poll every 3 seconds
            await asyncio.sleep(3)
            
        return self._chat_id

    async def send_message(self, text: str) -> bool:
        """Send a markdown-formatted message to the registered chat ID."""
        if not self.bot_token:
            return False
            
        # Dynamically resolve if not present
        if not self._chat_id:
            await self.resolve_chat_id()
            
        if not self._chat_id:
            print("⚠️ Telegram Chat ID not resolved yet. Send a message (e.g. /start) to the bot on Telegram first!")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        
        try:
            def post():
                data = urllib.parse.urlencode(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.read()

            await asyncio.to_thread(post)
            return True
        except Exception as e:
            print(f"Error sending message to Telegram: {e}")
            return False

    async def start_listening(self, runtime) -> None:
        """Background loop to poll `/getUpdates` and process user commands."""
        # Wait until we have a chat ID resolved (so we know who to reply to)
        if not self._chat_id:
            print("DEBUG: Waiting for Telegram Chat ID to be resolved before listening...")
            await self.resolve_chat_id()
            
        print(f"DEBUG: Starting Telegram Command listener loop for chat ID: {self._chat_id}...")
        offset = 0
        
        # Load offset from updates first to avoid re-running old commands on startup
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
            def fetch_initial():
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode())
            data = await asyncio.to_thread(fetch_initial)
            results = data.get("result", [])
            if results:
                offset = max(r["update_id"] for r in results) + 1
        except Exception as e:
            print(f"DEBUG: Failed to initialize Telegram update offset: {e}")

        while True:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={offset}&timeout=10"
                def fetch_updates():
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15) as response:
                        return json.loads(response.read().decode())

                data = await asyncio.to_thread(fetch_updates)
                results = data.get("result", [])
                for r in results:
                    offset = max(offset, r["update_id"] + 1)
                    
                    message = r.get("message")
                    if not message:
                        continue
                        
                    chat = message.get("chat", {})
                    chat_id = str(chat.get("id"))
                    
                    # Only accept commands from our resolved chat ID
                    if chat_id != self._chat_id:
                        print(f"DEBUG: Ignoring message from unauthorized chat: {chat_id}")
                        continue
                        
                    text = message.get("text", "").strip()
                    if not text.startswith("/"):
                        continue
                        
                    command = text.split()[0].lower()
                    print(f"DEBUG: Received Telegram command: {command}")
                    
                    await self._handle_command(command, runtime)
            except Exception as e:
                print(f"Error polling Telegram updates: {e}")
                
            await asyncio.sleep(2)

    def _recalculate_open_positions_pnl(self, runtime) -> None:
        """Dynamically recalculate MTM PnL for all open positions in memory using current LTPs."""
        if not hasattr(runtime, "_token_ltp") or not runtime._token_ltp:
            return
        
        ltp_map = runtime._token_ltp
        from services.chartedge_core.models import Direction
        
        def resolve_symbol_to_token_id(symbol: str, runtime) -> Optional[str]:
            if not symbol:
                return None
            stripped = symbol.split(":", 1)[1] if ":" in symbol else symbol
            if stripped.isdigit():
                return stripped
            if not hasattr(runtime, "dm") or not runtime.dm:
                return None
            try:
                if getattr(runtime.dm, "_fno_df", None) is None:
                    runtime.dm._fetch_fno_master()
                df = runtime.dm._fno_df
                if df is not None:
                    mask = df['TRADING_SYMBOL'].str.upper() == symbol.upper()
                    res = df[mask]
                    if not res.empty:
                        return str(int(res.iloc[0]['SECURITY_ID']))
            except Exception as e:
                print(f"Error resolving symbol {symbol} to token ID: {e}")
            return None

        # 1. Options Positions
        if hasattr(runtime, "trader") and runtime.trader:
            for trade in runtime.trader.open_positions.values():
                # Leg-based MTM logic
                if getattr(trade, "legs", []):
                    net_price = 0.0
                    all_legs_priced = True
                    for leg in trade.legs:
                        leg_price = ltp_map.get(leg.instrument)
                        if leg_price is None:
                            stripped = leg.instrument.split(":", 1)[1] if ":" in leg.instrument else leg.instrument
                            leg_price = ltp_map.get(stripped)
                        if leg_price is None:
                            token_id = resolve_symbol_to_token_id(leg.instrument, runtime)
                            if token_id:
                                leg_price = ltp_map.get(token_id)
                        if leg_price is None:
                            all_legs_priced = False
                            break
                        multiplier = 1 if leg.action == Direction.BUY else -1
                        net_price += (leg_price * leg.ratio * multiplier)
                    if all_legs_priced:
                        current_price = abs(round(net_price, 2))
                        direction_mult = 1 if trade.direction == Direction.BUY else -1
                        trade.pnl = round((current_price - trade.entry_price) * trade.quantity * direction_mult, 2)
                        trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2) if trade.entry_price > 0 and trade.quantity > 0 else 0.0
                else:
                    # Single leg or direct instrument
                    current_price = ltp_map.get(trade.instrument)
                    if current_price is None:
                        stripped = trade.instrument.split(":", 1)[1] if ":" in trade.instrument else trade.instrument
                        current_price = ltp_map.get(stripped)
                    if current_price is not None:
                        direction_mult = 1 if trade.direction == Direction.BUY else -1
                        trade.pnl = round((current_price - trade.entry_price) * trade.quantity * direction_mult, 2)
                        trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 2) if trade.entry_price > 0 and trade.quantity > 0 else 0.0

        # 2. Futures Positions
        if hasattr(runtime, "futures_trader") and runtime.futures_trader:
            for trade in runtime.futures_trader.open_positions.values():
                current_price = ltp_map.get(trade.instrument)
                if current_price is None:
                    stripped = trade.instrument.split(":", 1)[1] if ":" in trade.instrument else trade.instrument
                    current_price = ltp_map.get(stripped)
                # Fallback to NIFTY spot or FUT
                if current_price is None and "NIFTY" in trade.instrument.upper():
                    current_price = ltp_map.get("NIFTY_FUT") or ltp_map.get("NIFTY")
                if current_price is not None:
                    direction_mult = 1 if trade.direction == Direction.BUY else -1
                    trade.pnl = round((current_price - trade.entry_price) * direction_mult * trade.quantity, 2)
                    trade.pnl_pct = round(trade.pnl / trade.invested_amount * 100, 2) if trade.invested_amount > 0 else 0.0

    async def _handle_command(self, command: str, runtime) -> None:
        self._recalculate_open_positions_pnl(runtime)
        if command in ("/start", "/help"):
            msg = (
                "🤖 *ChartEdge AI Commands:*\n\n"
                "📊 `/positions` - List all active open positions\n"
                "💰 `/pnl` - Get realized & unrealized PnL summary\n"
                "🏥 `/status` - Check server status and feed health\n"
                "ℹ️ `/help` - Show this help menu"
            )
            await self.send_message(msg)
            
        elif command == "/status":
            open_opts = len(runtime.trader.open_positions) if hasattr(runtime, "trader") else 0
            open_futs = len(runtime.futures_trader.open_positions) if hasattr(runtime, "futures_trader") else 0
            data_src = getattr(runtime, "data_source", "unknown")
            if hasattr(runtime, "config") and hasattr(runtime.config, "data"):
                data_src = runtime.config.data.get("source", data_src)
            msg = (
                f"🏥 *System Status:*\n\n"
                f"⏱️ *Time:* `{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S') if 'IST' in globals() else datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
                f"📡 *Data Source:* `{data_src}`\n"
                f"❤️ *Feed Health:* `{runtime.feed_health}`\n"
                f"📂 *Open Positions:* `{open_opts + open_futs}` (Options: {open_opts}, Futures: {open_futs})"
            )
            await self.send_message(msg)
            
        elif command == "/positions":
            opt_positions = list(runtime.trader.open_positions.values()) if hasattr(runtime, "trader") else []
            fut_positions = list(runtime.futures_trader.open_positions.values()) if hasattr(runtime, "futures_trader") else []
            
            if not opt_positions and not fut_positions:
                await self.send_message("📂 *Positions:*\n\nNo active open positions.")
                return
                
            msg = "📂 *Active Positions:*\n\n"
            
            if opt_positions:
                msg += "*Options Positions:*\n"
                
                def resolve_token_to_symbol(token_id: str, runtime) -> str:
                    if not token_id:
                        return ""
                    stripped = token_id.split(":", 1)[1] if ":" in token_id else token_id
                    if not stripped.isdigit():
                        return token_id
                    if not hasattr(runtime, "dm") or not runtime.dm:
                        return token_id
                    try:
                        if getattr(runtime.dm, "_fno_df", None) is None:
                            runtime.dm._fetch_fno_master()
                        df = runtime.dm._fno_df
                        if df is not None:
                            mask = df['SECURITY_ID'] == int(stripped)
                            res = df[mask]
                            if not res.empty:
                                return str(res.iloc[0]['TRADING_SYMBOL'])
                    except Exception:
                        pass
                    return token_id

                for trade in opt_positions:
                    pnl_emoji = "🟢" if trade.pnl >= 0 else "🔴"
                    
                    import re
                    from datetime import datetime
                    display_inst = trade.instrument
                    match = re.search(r'_(\d{2}[A-Z]{3}\d{2})', trade.instrument)
                    if match:
                        try:
                            dt = datetime.strptime(match.group(1), "%d%b%y")
                            display_inst = f"{trade.instrument} (Expiry: {dt.strftime('%d-%b-%Y')})"
                        except:
                            pass
                            
                    msg += (
                        f"• `{display_inst}` ({trade.direction.value})\n"
                        f"  Qty: {trade.quantity} | Entry: `₹{trade.entry_price:.2f}`\n"
                        f"  SL: `₹{trade.sl_price:.2f}` | T1: `₹{trade.t1_price:.2f}`\n"
                        f"  PnL: {pnl_emoji} `₹{trade.pnl:.2f}` (`{trade.pnl_pct:+.2f}%`)\n"
                    )
                    if getattr(trade, "legs", []):
                        msg += "  _Legs:_\n"
                        for leg in trade.legs:
                            leg_name = resolve_token_to_symbol(leg.instrument, runtime)
                            msg += f"    - {leg.action.value} `{leg_name}` @ `₹{leg.entry_price:.2f}` (Strike: {leg.strike})\n"
                msg += "\n"
                
            if fut_positions:
                msg += "*Futures Positions:*\n"
                for trade in fut_positions:
                    pnl_emoji = "🟢" if trade.pnl >= 0 else "🔴"
                    
                    import re
                    from datetime import datetime
                    display_inst = trade.instrument
                    match = re.search(r'_(\d{2}[A-Z]{3}\d{2})', trade.instrument)
                    if match:
                        try:
                            dt = datetime.strptime(match.group(1), "%d%b%y")
                            display_inst = f"{trade.instrument} (Expiry: {dt.strftime('%d-%b-%Y')})"
                        except:
                            pass
                            
                    msg += (
                        f"• `{display_inst}` ({trade.direction})\n"
                        f"  Qty: {trade.quantity} | Entry: `₹{trade.entry_price:.2f}`\n"
                        f"  SL: `₹{trade.sl_price:.2f}` | T1: `₹{trade.t1_price:.2f}`\n"
                        f"  PnL: {pnl_emoji} `₹{trade.pnl:.2f}` (`{trade.pnl_pct:+.2f}%`)\n"
                    )
            await self.send_message(msg)
            
        elif command == "/pnl":
            open_opts = list(runtime.trader.open_positions.values()) if hasattr(runtime, "trader") else []
            raw_closed_opts = list(runtime.trader.closed_trades) if hasattr(runtime, "trader") else []
            
            # Filter options: exclude futures
            closed_opts = [t for t in raw_closed_opts if not ("_FUT" in t.instrument or t.instrument.endswith("_FUT"))]
            
            unrealized_opts = sum(t.pnl for t in open_opts)
            realized_opts = sum(t.pnl for t in closed_opts)
            
            open_futs = list(runtime.futures_trader.open_positions.values()) if hasattr(runtime, "futures_trader") else []
            closed_futs = list(runtime.futures_trader.closed_trades) if hasattr(runtime, "futures_trader") else []
            
            # Also pull closed futures from raw_closed_opts if they exist there but not in closed_futs
            for t in raw_closed_opts:
                if "_FUT" in t.instrument or t.instrument.endswith("_FUT"):
                    if str(t.id) not in [str(x.id) for x in closed_futs]:
                        closed_futs.append(t)
                        
            unrealized_futs = sum(t.pnl for t in open_futs)
            realized_futs = sum(t.pnl for t in closed_futs)
            
            total_unrealized = unrealized_opts + unrealized_futs
            total_realized = realized_opts + realized_futs
            total_pnl = total_unrealized + total_realized
            
            ur_emoji = "🟢" if total_unrealized >= 0 else "🔴"
            re_emoji = "🟢" if total_realized >= 0 else "🔴"
            tot_emoji = "🟢" if total_pnl >= 0 else "🔴"
            
            msg = (
                f"💰 *PnL Summary:*\n\n"
                f"*Unrealized PnL:* {ur_emoji} `₹{total_unrealized:.2f}`\n"
                f"  - Options: `₹{unrealized_opts:.2f}`\n"
                f"  - Futures: `₹{unrealized_futs:.2f}`\n\n"
                f"*Realized PnL:* {re_emoji} `₹{total_realized:.2f}`\n"
                f"  - Options: `₹{realized_opts:.2f}`\n"
                f"  - Futures: `₹{realized_futs:.2f}`\n\n"
                f"*Total PnL:* {tot_emoji} `₹{total_pnl:.2f}`"
            )
            await self.send_message(msg)
        else:
            await self.send_message("❓ Unknown command. Type `/help` for list of commands.")

# Global notifier instance
notifier = TelegramNotifier()

