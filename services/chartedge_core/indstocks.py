from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import websockets
from httpx import HTTPStatusError

from services.chartedge_core.config import Config
from services.chartedge_core.confluence import score
from services.chartedge_core.indicators import compute_snapshot_indicators
from services.chartedge_core.models import Candle, Direction, IndicatorSnapshot
from services.chartedge_core.simulation import MarketSimulator


IST = ZoneInfo("Asia/Kolkata")


class IndstocksMarketRuntime(MarketSimulator):
    """Live INDstocks market-data runtime.

    REST historical candles warm the indicator windows; the price websocket then
    builds one-minute candles from LTP ticks and feeds the same signal/paper
    trading pipeline used by the simulator.
    """

    def __init__(self, config: Config, skip_db_load: bool = False) -> None:
        super().__init__(config, skip_db_load=skip_db_load)
        self.indstocks = config.data["indstocks"]
        self._active_candles: dict[str, Candle] = {}
        # Map both stripped ("40000001") and full ("NIDX:40000001") token formats
        self._token_to_symbol = {}
        for item in config.instruments:
            if not item.get("enabled", True):
                continue
            full_token = item["websocket_token"]
            stripped = full_token.split(":", 1)[1] if ":" in full_token else full_token
            self._token_to_symbol[stripped] = item["symbol"]
            self._token_to_symbol[full_token] = item["symbol"]
        from services.chartedge_core.derivative_manager import DerivativeManager
        token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
        # Initialize even without token to use cache if available
        self.dm = DerivativeManager(token or "DUMMY_TOKEN")
        self._cached_deriv_tokens: dict = {}
        self._last_deriv_update = datetime.min
        self._token_oi = {} # Track real-time OI
        self._token_ltp = {} # Track real-time LTP

    async def seed(self) -> None:
        print("\n" + "="*50)
        print("📥 STARTING HISTORICAL BACKFILL (SEEDING)")
        print("="*50)
        
        self.feed_health = "BACKFILLING"
        token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
        if not token:
            self.feed_health = "INDSTOCKS_TOKEN_MISSING"
            print("❌ ABORTED: INDSTOCKS_TOKEN_MISSING")
            return
        
        # Strip 'Bearer ' prefix if present
        if token.startswith("Bearer "):
            token = token[7:]

        end = datetime.now(IST)
        start = end - timedelta(days=int(self.indstocks["historical_lookback_days"]))

        # Disable AI and set seeding flag
        prev_ai = self.signal_engine.ai_enabled
        self.signal_engine.ai_enabled = False
        self.is_seeding = True

        all_candles: list[Candle] = []
        try:
            # 1. Parallel Fetch for all enabled instruments
            for instrument in self.config.instruments:
                if not instrument.get("enabled", True):
                    continue
                
                symbol = instrument["symbol"]
                websocket_token = instrument["websocket_token"]
                
                print(f"📡 Fetching historical data for {symbol}...")
                try:
                    # Run sync httpx.get off the event loop so HTTP handler tasks aren't starved
                    candles = await asyncio.to_thread(self._fetch_historical, token, instrument, start, end)
                    if candles:
                        print(f"✅ Received {len(candles)} candles for {symbol}")
                        all_candles.extend(candles)
                    else:
                        print(f"⚠️ No data found for {symbol}")
                except Exception as e:
                    print(f"❌ Failed to fetch {symbol}: {e}")

            # 2. Sort all candles by time to maintain timeline integrity
            print(f"⚖️ Sorting {len(all_candles)} candles chronologically...")
            all_candles.sort(key=lambda c: c.time)

            # 3. Process sequentially
            print("🚀 Replaying market events...")
            for candle in all_candles:
                symbol = candle.instrument
                if symbol in self.config.trading_symbols:
                    await self.process_closed_candle(candle)
                    self._append_equity(candle.time)
                else:
                    # Non-trading symbols (VIX, monitors)
                    self.candles[symbol].append(candle)
                    self.market_data_history[symbol].append({"time": candle.time.isoformat(), "price": candle.close})
                    # Manually update indicators for these
                    if len(self.candles[symbol]) % 5 == 0: # Efficiency: update every 5 candles
                        self._update_latest_indicators(symbol)

            print("="*50)
            print("✅ BACKFILL COMPLETE - SYSTEM READY")
            print("="*50 + "\n")
            self.feed_health = "OK"
        except Exception as exc:
            self.feed_health = f"INDSTOCKS_BACKFILL_FAILED:{exc.__class__.__name__}"
            print(f"❌ BACKFILL FAILED: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_seeding = False
            self.signal_engine.ai_enabled = prev_ai

    def _update_latest_indicators(self, symbol: str):
        """Helper to update indicators during backfill without full process_closed_candle overhead."""
        if len(self.candles[symbol]) < 30:
            return
            
        last_candle = self.candles[symbol][-1]
        weights = self.config.indicator_weights.get(symbol, {})
        if not weights:
            weights = {"rsi": 0.15, "macd": 0.15, "ema_ribbon": 0.15, "vwap": 0.20, "supertrend": 0.25, "volume": 0.10}
        
        indicators = compute_snapshot_indicators(list(self.candles[symbol]), weights)
        snapshot = IndicatorSnapshot(
            instrument=symbol,
            timeframe="5m",
            candle_time=last_candle.time,
            price=last_candle.close,
            indicators=indicators,
            confluence_score=score(indicators),
            higher_timeframe=self._higher_timeframe(symbol),
            market_context=self._get_market_context(),
            options_data=self._get_options_data(symbol) if symbol in ["NIFTY", "BANKNIFTY"] else None
        )
        self.latest_indicators[symbol] = snapshot

    async def run_backtest(self, start: datetime, end: datetime) -> dict[str, int | str]:
        token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
        if not token:
            self.feed_health = "INDSTOCKS_TOKEN_MISSING"
            return {"status": "error", "reason": "INDSTOCKS_TOKEN_MISSING"}

        self.reset_runtime_state()
        self.is_backtesting = True
        self.trader.is_backtesting = True
        fetched_counts: dict[str, int] = {}
        all_candles: list[Candle] = []
        try:
            for instrument in self.config.instruments:
                if not instrument.get("enabled", True):
                    continue
                try:
                    print(f"DEBUG: Fetching historical for {instrument['symbol']}...")
                    candles = await asyncio.to_thread(self._fetch_historical, token, instrument, start, end)
                    print(f"DEBUG: Fetched {len(candles)} candles for {instrument['symbol']}")
                    fetched_counts[instrument["symbol"]] = len(candles)
                    all_candles.extend(candles)
                except HTTPStatusError as exc:
                    fetched_counts[instrument["symbol"]] = 0
                    self.feed_health = "BACKTEST_FETCH_FAILED"
                    return {
                        "status": "error",
                        "instrument": instrument["symbol"],
                        "http_status": exc.response.status_code,
                        "body": exc.response.text[:500],
                        **fetched_counts,
                    }

            for candle in sorted(all_candles, key=lambda item: item.time):
                await self.process_closed_candle(candle)
                self._append_equity(candle.time)

            if self.trader.open_positions:
                last_prices = {
                    symbol: candles[-1].close
                    for symbol, candles in self.candles.items()
                    if candles
                }
                await self.trader.force_close_all(last_prices, end, "BACKTEST_EOD")
                self._append_equity(end)

            self.feed_health = "BACKTEST_COMPLETE"
            return {"status": "ok", **fetched_counts}
        finally:
            self.is_backtesting = False
            self.trader.is_backtesting = False

    def _fetch_historical(
        self, token: str, instrument: dict, start: datetime, end: datetime
    ) -> list[Candle]:
        for attempt in range(3):
            try:
                response = httpx.get(
                    f"{self.indstocks['base_url']}/market/historical/{self.indstocks['historical_interval']}",
                    headers={"Authorization": token},
                    params={
                        "scrip-codes": instrument["historical_scrip_code"],
                        "start_time": int(start.timestamp() * 1000),
                        "end_time": int(end.timestamp() * 1000),
                    },
                    timeout=12,
                )
                if response.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                response.raise_for_status()
                break
            except Exception as e:
                if attempt == 2: raise e
                time.sleep(1)
        payload = response.json()
        raw_candles = self._extract_historical_candles(payload, instrument["historical_scrip_code"])
        return [self._historical_row_to_candle(row, instrument["symbol"]) for row in raw_candles]

    def _extract_historical_candles(self, payload: dict, scrip_code: str) -> list:
        data = payload.get("data", {})
        if isinstance(data.get("candles"), list):
            return data["candles"]
        if isinstance(data.get(scrip_code), dict) and isinstance(data[scrip_code].get("candles"), list):
            return data[scrip_code]["candles"]
        return []

    def _historical_row_to_candle(self, row: list | dict, symbol: str) -> Candle:
        if isinstance(row, dict):
            timestamp = int(row["ts"])
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            return Candle(
                time=datetime.fromtimestamp(timestamp, IST),
                instrument=symbol,
                timeframe="1m",
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=int(row.get("v", 0)),
            )
        return Candle(
            time=datetime.fromtimestamp(row[0] / 1000, IST),
            instrument=symbol,
            timeframe="1m",
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=int(row[5]),
        )

    async def run(self) -> None:
        """Main loop: seed history once, then maintain via websocket ticks."""
        from dotenv import load_dotenv
        retry_delay = 5
        _seeded = False
        
        # Start the background wall-clock EOD square-off watchdog task exactly once
        if not hasattr(self, "_watchdog_started"):
            self._watchdog_started = True
            asyncio.create_task(self._eod_watchdog_loop())

        while True:
            try:
                print("DEBUG: Starting IndstocksMarketRuntime.run()")
                if not _seeded:
                    await self.seed()
                    _seeded = True
                
                # Reload .env to pick up any manual token updates without restarting the server
                load_dotenv(override=True)
                token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
                if token and token.startswith("Bearer "):
                    token = token[7:]
                
                ws_url = self.indstocks["websocket_url"]
                print(f"DEBUG: Connecting to {ws_url}")
                async with websockets.connect(
                    ws_url,
                    additional_headers={"Authorization": token},
                    ping_interval=30,
                    ping_timeout=10,
                ) as websocket:
                    await websocket.send(
                        json.dumps(
                            {
                                                    "action": "subscribe",
                                "mode": self.indstocks.get("websocket_mode", "full"),
                                "instruments": self._get_subscription_tokens(),
                            }
                        )
                    )
                    print(f"DEBUG: Websocket connected and subscription sent for {len(self._get_subscription_tokens())} tokens")
                    self.feed_health = "OK"
                    retry_delay = 5 # Reset delay on success
                    
                    last_heartbeat = time.time()
                    stale_timeout = float(self.config.risk.get("stale_feed_seconds", 60))
                    while True:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=stale_timeout)
                        except asyncio.TimeoutError:
                            now_ist = datetime.now(IST)
                            if self._is_market_open(now_ist):
                                print(f"⚠️ WARNING: No websocket ticks received during market hours for {stale_timeout}s. Forcing reconnect...")
                                raise RuntimeError("WebSocket read timeout (stale feed)")
                            else:
                                if time.time() - last_heartbeat > 30:
                                    print("DEBUG: WebSocket Loop Heartbeat - Active (Non-Market Hours / Quiet)")
                                    last_heartbeat = time.time()
                                continue

                        print(f"DEBUG: Raw message received: {message[:100]}...")
                        if "40000001" in message:
                            with open("raw_ticks.log", "a") as rf:
                                rf.write(message + "\n")
                        if time.time() - last_heartbeat > 30:
                            print("DEBUG: WebSocket Loop Heartbeat - Active")
                            last_heartbeat = time.time()
                        
                        try:
                            payload = json.loads(message)
                            if isinstance(payload, str):
                                payload = json.loads(payload)
                            
                            if isinstance(payload, list):
                                for tick in payload:
                                    await self._handle_tick(tick)
                            else:
                                await self._handle_tick(payload)
                                
                        except Exception as e:
                            print("WS TICK ERROR:", e)
                            
            except Exception as e:
                self.feed_health = f"RECONNECTING:{e.__class__.__name__}"
                print(f"DEBUG: Websocket loop failed: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    async def _eod_watchdog_loop(self) -> None:
        """Background watchdog to enforce EOD square-off based on system wall-clock time."""
        print("🕒 EOD Wall-Clock Watchdog Started")
        while True:
            try:
                await asyncio.sleep(15) # Run every 15 seconds
                
                sq_time_str = self.config.market_hours.get("square_off")
                if not sq_time_str:
                    continue
                    
                sq_hour, sq_min = map(int, sq_time_str.split(":"))
                now = datetime.now(IST)
                
                # Only check on valid trading days (not weekends/holidays)
                if not self._is_trading_day(now):
                    continue
                    
                # If we have passed the square-off time (e.g. 15:00 IST)
                if now.hour > sq_hour or (now.hour == sq_hour and now.minute >= sq_min):
                    if not self.is_seeding and not self.is_backtesting:
                        if self.trader.open_positions:
                            print(f"🕒 EOD Wall-Clock Watchdog: EOD Boundary Hit at system time {now.strftime('%H:%M:%S')}")
                            prices = {}
                            for symbol in self.config.trading_symbols:
                                if self.candles.get(symbol):
                                    prices[symbol] = self.candles[symbol][-1].close
                                elif symbol in self._token_ltp:
                                    prices[symbol] = self._token_ltp[symbol]
                                else:
                                    prices[symbol] = 0.0
                                    
                            await self.trader.force_close_all(prices, now, "EOD_SQUAREOFF")
            except Exception as e:
                print(f"❌ ERROR inside EOD Watchdog: {e}")

    def _get_subscription_tokens(self) -> list[str]:
        tokens = [
            item["websocket_token"]
            for item in self.config.instruments
            if item.get("enabled", True)
        ]
        
        # Add Option Tokens for all trading symbols
        for item in self.config.instruments:
            symbol = item["symbol"]
            if item.get("role") == "trading" and self.candles.get(symbol) and self.dm:
                try:
                    spot = self.candles[symbol][-1].close
                    chain = self.dm.get_option_chain(spot, symbol, range_strikes=10)
                    for row in chain:
                        if row["ce_token"]: tokens.append(row["ce_token"])
                        if row["pe_token"]: tokens.append(row["pe_token"])
                except Exception as e:
                    print(f"FAILED_TO_GET_OPTION_CHAIN_TOKENS for {symbol}: {e}")
        
        return list(set(tokens))

    async def _handle_tick(self, message: dict) -> None:
        if not isinstance(message, dict) or "data" not in message:
            return
        
        raw_token = str(message.get("instrument", ""))
        # Normalize: strip exchange prefix so "NIDX:40000001" and "40000001" both work
        token_id = raw_token.split(":", 1)[1] if ":" in raw_token else raw_token
        print(f"DEBUG: Received tick for token {token_id}")

        # Track OI for all tokens (including options)
        # Robust price extraction
        data = message.get("data", {})
        ltp = float(data.get("ltp") or data.get("last_price") or data.get("price") or data.get("lp") or data.get("close") or 0)

        if ltp > 0:
            self._token_ltp[token_id] = ltp
            symbol = self._token_to_symbol.get(token_id)
            if symbol:
                print(f"DEBUG: Tick -> Symbol: {symbol}, Price: {ltp}")

        # Track OI
        oi = data.get("oi")
        if oi is not None:
            self._token_oi[token_id] = float(oi)

        if ltp == 0:
            return

        symbol = self._token_to_symbol.get(token_id)
        if not symbol:
            return

        raw_ts = int(message.get("timestamp", 0))
        if raw_ts == 0:
            return
        tick_time = datetime.fromtimestamp(raw_ts / 1000, IST).replace(
            second=0, microsecond=0
        )

        if self.feed_health in ["RESET", "BACKFILLING"]:
            self.feed_health = "OK"

        active = self._active_candles.get(symbol)
        if active and active.time < tick_time:
            # Move candle processing to background so AI latency doesn't block WS feed
            asyncio.create_task(self.process_closed_candle(active))
            self._append_equity(tick_time)
            del self._active_candles[symbol]
            active = None

        if not active:
            self._active_candles[symbol] = Candle(
                time=tick_time,
                instrument=symbol,
                timeframe="1m",
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=1,
            )
            return

        active.high = max(active.high, ltp)
        active.low = min(active.low, ltp)
        active.close = ltp
        active.volume += 1

    def _get_market_context(self) -> "MarketContext":
        from services.chartedge_core.models import MarketContext, OptionChainData
        
        ctx = super()._get_market_context()
        
        # We only enhance context for NIFTY
        if not self.dm or "NIFTY" not in self.candles:
            return ctx
            
        # Periodically refresh tokens (every 4 hours)
        now = datetime.now()
        if (now - self._last_deriv_update).total_seconds() > 14400:
            try:
                if self.candles.get("NIFTY") and len(self.candles["NIFTY"]) > 0:
                    spot = self.candles["NIFTY"][-1].close
                    opts = self.dm.get_atm_options(spot, "NIFTY")
                    # Extract only tokens for caching (as used by global cooldown logic)
                    self._cached_deriv_tokens = {
                        "future": self.dm.get_current_future("NIFTY"),
                        "options": {k: v["token"] for k, v in opts.items()}
                    }
                    self._last_deriv_update = now
            except Exception as e:
                print(f"DERIV_TOKEN_UPDATE_FAILED: {e}")
                # Set cooldown of 5 minutes before trying again to avoid hammering CPU on every tick
                from datetime import timedelta
                self._last_deriv_update = now - timedelta(seconds=14400 - 300)

        # In a real live run, we would fetch quotes here for basis/oi.
        # For now, we return the base context with placeholder basis if not fetching yet.
        return ctx

    def _get_options_data(self, symbol: str) -> "OptionChainData":
        from services.chartedge_core.models import OptionChainData, OptionChainRow
        if symbol not in self.candles:
            return None
            
        spot = self.candles[symbol][-1].close
        
        chain_rows = []
        total_ce_oi = 0
        total_pe_oi = 0
        max_ce_oi = 0
        max_pe_oi = 0
        res_wall = 0
        sup_wall = 0

        if self.dm and spot > 0:
            try:
                raw_chain = self.dm.get_option_chain(spot, symbol, range_strikes=10)
                for row in raw_chain:
                    ce_token_id = row["ce_token"].split(":", 1)[1] if row["ce_token"] else ""
                    pe_token_id = row["pe_token"].split(":", 1)[1] if row["pe_token"] else ""
                    
                    ce_oi = self._token_oi.get(ce_token_id, 0)
                    pe_oi = self._token_oi.get(pe_token_id, 0)
                    
                    total_ce_oi += ce_oi
                    total_pe_oi += pe_oi
                    
                    if ce_oi > max_ce_oi:
                        max_ce_oi = ce_oi
                        res_wall = row["strike"]
                    if pe_oi > max_pe_oi:
                        max_pe_oi = pe_oi
                        sup_wall = row["strike"]

                    chain_rows.append(OptionChainRow(
                        strike=row["strike"],
                        ce_token=row["ce_token"],
                        pe_token=row["pe_token"]
                    ))
            except Exception as e:
                print(f"FAILED_TO_GET_LIVE_CHAIN_DATA for {symbol}: {e}")
        
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 1.05
        
        # Default walls if no OI data yet
        strike_step = 100 if "BANK" in symbol else (25 if "MIDCP" in symbol else 50)
        
        return OptionChainData(
            pcr=pcr,
            resistance_wall=res_wall or round(spot + (strike_step * 3), -2),
            support_wall=sup_wall or round(spot - (strike_step * 3), -2),
            chain=chain_rows[:11]
        )

    def get_option_contract(self, symbol: str, direction: str) -> Optional[dict]:
        """Resolves an index signal to an ATM option contract.
        
        Note: Per user request, options are strictly limited to NIFTY.
        """
        if not self.dm:
            return None
            
        # Restrict options to NIFTY and BANKNIFTY
        if symbol not in ["NIFTY", "BANKNIFTY"]:
            return None
            
        spot = self.candles[symbol][-1].close if self.candles.get(symbol) else 0
        if spot <= 0: return None
        
        try:
            options = self.dm.get_atm_options(spot, symbol)
            # Support both Direction.value (BUY/SELL) and explicit type (CE/PE)
            if direction in ["CE", "PE"]:
                opt_type = direction
            else:
                opt_type = "CE" if direction == "BUY" else "PE"
            
            contract_data = options.get(opt_type)
            if not contract_data: return None
            
            token_id = contract_data["token"].split(":", 1)[1]
            return {
                "symbol": contract_data["symbol"], # Now includes Expiry and Strike from IndMoney master
                "token": contract_data["token"],
                "ltp": self._token_ltp.get(token_id, 0)
            }
        except Exception as e:
            print(f"ERROR_RESOLVING_OPTION: {e}")
            return None
