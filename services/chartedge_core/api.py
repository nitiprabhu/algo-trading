from __future__ import annotations
print("DEBUG: Loading api.py")

import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv

# Load .env before other imports that might use env vars
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from typing import Optional

print("DEBUG: Importing config")
from services.chartedge_core.config import load_config, apply_db_overrides, sync_config_to_db
print("DEBUG: Importing indstocks")
from services.chartedge_core.indstocks import IndstocksMarketRuntime
print("DEBUG: Importing simulation")
from services.chartedge_core.simulation import IST, MarketSimulator
from services.chartedge_core.database import cleanup_expired_records


config = load_config()
data_source = os.getenv("CHARTEDGE_DATA_SOURCE", config.data.get("source", "mock"))
print(f"DEBUG: Data source: {data_source}")
print("DEBUG: Initializing runtime...")
runtime = IndstocksMarketRuntime(config) if data_source == "indstocks" else MarketSimulator(config)
print("DEBUG: Runtime initialized")

# Positional (weekly options) engine(s) -- fully separate from intraday, own
# capital, own JSON log. Only active if shared/config.yaml positional_risk.enabled: true.
# mode: single -> one engine (positional_engines has 1 entry).
# mode: parallel -> one engine per shared/config.yaml positional_risk.parallel
# entry, each with its own capital pool -- lets all 4 strategies run and
# accrue their own track record at once, since a weekly cycle can't be
# switched mid-week anyway. positional_engine/positional_runtime_wrapper are
# kept as aliases to the first engine for backward compatibility.
positional_engines: list = []
positional_runtime_wrappers: list = []
_positional_cfg = config.positional_risk or {}
if _positional_cfg.get("enabled", False):
    from services.chartedge_core.positional_trading import PositionalTradingEngine
    from services.chartedge_core.positional_runtime import PositionalRuntime
    from services.chartedge_core.positional_data_provider import IndstocksDataProvider, UpstoxDataProvider

    # Data source is a plugin, not a full runtime swap: only spot/VIX/option
    # chain/premium resolution is affected. One provider instance is shared
    # across every engine (mode: parallel) since they all read the same
    # NIFTY chain -- avoids redundant REST calls. env var mirrors the
    # existing CHARTEDGE_DATA_SOURCE pattern (api.py:27).
    _positional_data_source = os.getenv("POSITIONAL_DATA_SOURCE", _positional_cfg.get("data_source", "indstocks"))
    if _positional_data_source == "upstox":
        _positional_provider = UpstoxDataProvider()
        print("DEBUG: Weekly positional data source: upstox")
    else:
        _positional_provider = IndstocksDataProvider(runtime)
        print("DEBUG: Weekly positional data source: indstocks")

    if _positional_cfg.get("mode", "single") == "parallel":
        _parallel_specs = _positional_cfg.get("parallel") or []
        for spec in _parallel_specs:
            # A malformed entry (missing/misspelled `strategy`) must not take
            # down the whole API process -- this block runs at import time,
            # before FastAPI even starts, so an uncaught ValueError here kills
            # intraday trading too, not just the positional module.
            try:
                eng = PositionalTradingEngine(
                    capital=spec.get("capital", 100000.0),
                    strategy_name=spec.get("strategy"),
                )
            except ValueError as e:
                print(f"⚠️ [Positional] Skipping malformed parallel entry {spec!r}: {e}")
                continue
            positional_engines.append(eng)
            positional_runtime_wrappers.append(PositionalRuntime(eng, _positional_cfg, _positional_provider))
        print(f"DEBUG: Positional trading engines enabled (parallel): {[e.strategy_name for e in positional_engines]}")
    else:
        eng = PositionalTradingEngine(
            capital=_positional_cfg.get("capital", 100000.0),
            strategy_name=_positional_cfg.get("strategy", "condor"),
        )
        positional_engines.append(eng)
        positional_runtime_wrappers.append(PositionalRuntime(eng, _positional_cfg, _positional_provider))
        print("DEBUG: Positional trading engine enabled (single)")

positional_engine = positional_engines[0] if positional_engines else None
positional_runtime_wrapper = positional_runtime_wrappers[0] if positional_runtime_wrappers else None

# Positional stocks (large-cap technical investment) engine -- fully separate
# from intraday and from the weekly options positional module, own capital,
# own DB table. Long-only: BUY opens, SELL only closes. Only active if
# shared/config.yaml positional_stocks_risk.enabled: true.
positional_stocks_engine = None
positional_stocks_runtime_wrapper = None
_positional_stocks_cfg = config.positional_stocks_risk or {}
if _positional_stocks_cfg.get("enabled", False):
    from services.chartedge_core.positional_stocks import PositionalStocksEngine
    from services.chartedge_core.positional_stocks_runtime import PositionalStocksRuntime
    positional_stocks_engine = PositionalStocksEngine(
        capital=_positional_stocks_cfg.get("capital", 100000.0),
        max_positions=_positional_stocks_cfg.get("max_positions", 4),
        stop_loss_pct=_positional_stocks_cfg.get("stop_loss_pct", 6.0),
        target_pct=_positional_stocks_cfg.get("target_pct", 12.0),
    )
    positional_stocks_runtime_wrapper = PositionalStocksRuntime(positional_stocks_engine, _positional_stocks_cfg)
    print("DEBUG: Positional stocks engine enabled")

# Positional stocks (midcap + Nifty Next50 technical investment) engine --
# sibling of the largecap module above, own capital pool ("midcap" tag on
# the shared StockPositionRecord table). Only active if shared/config.yaml
# positional_stocks_midcap_risk.enabled: true.
positional_stocks_midcap_engine = None
positional_stocks_midcap_runtime_wrapper = None
_positional_stocks_midcap_cfg = config.positional_stocks_midcap_risk or {}
if _positional_stocks_midcap_cfg.get("enabled", False):
    from services.chartedge_core.positional_stocks import PositionalStocksEngine
    from services.chartedge_core.positional_stocks_runtime import PositionalStocksRuntime
    positional_stocks_midcap_engine = PositionalStocksEngine(
        capital=_positional_stocks_midcap_cfg.get("capital", 100000.0),
        max_positions=_positional_stocks_midcap_cfg.get("max_positions", 8),
        stop_loss_pct=_positional_stocks_midcap_cfg.get("stop_loss_pct", 4.0),
        target_pct=_positional_stocks_midcap_cfg.get("target_pct", 12.0),
        pool="midcap",
        confidence_sizing=_positional_stocks_midcap_cfg.get("confidence_sizing", False),
    )
    positional_stocks_midcap_runtime_wrapper = PositionalStocksRuntime(
        positional_stocks_midcap_engine, _positional_stocks_midcap_cfg
    )
    print("DEBUG: Positional stocks midcap engine enabled")

# Positional stocks (smallcap + microcap technical investment) engine --
# sibling of the largecap/midcap modules above, own capital pool ("smallcap"
# tag). Only active if shared/config.yaml positional_stocks_smallcap_risk.enabled: true.
# Capital budget note: largecap (1L) + midcap (1L) + smallcap (1L) = 3L total,
# per explicit user-set portfolio-wide cap -- do not raise any pool's capital
# without cutting another to stay under 3L combined.
positional_stocks_smallcap_engine = None
positional_stocks_smallcap_runtime_wrapper = None
_positional_stocks_smallcap_cfg = config.positional_stocks_smallcap_risk or {}
if _positional_stocks_smallcap_cfg.get("enabled", False):
    from services.chartedge_core.positional_stocks import PositionalStocksEngine
    from services.chartedge_core.positional_stocks_runtime import PositionalStocksRuntime
    positional_stocks_smallcap_engine = PositionalStocksEngine(
        capital=_positional_stocks_smallcap_cfg.get("capital", 100000.0),
        max_positions=_positional_stocks_smallcap_cfg.get("max_positions", 10),
        stop_loss_pct=_positional_stocks_smallcap_cfg.get("stop_loss_pct", 4.0),
        target_pct=_positional_stocks_smallcap_cfg.get("target_pct", 12.0),
        pool="smallcap",
        confidence_sizing=_positional_stocks_smallcap_cfg.get("confidence_sizing", False),
    )
    positional_stocks_smallcap_runtime_wrapper = PositionalStocksRuntime(
        positional_stocks_smallcap_engine, _positional_stocks_smallcap_cfg
    )
    print("DEBUG: Positional stocks smallcap engine enabled")

# Live-trading broker singleton (Upstox REST). Initialized from the
# live_trading config block; defaults keep it OFF + dry-run so importing/
# starting the app never risks a real order. The positional runtimes call
# live_broker() (no args) to reach this same instance.
from services.chartedge_core.upstox_broker import live_broker as _init_live_broker
_live_trading_cfg = config.live_trading or {}
_init_live_broker(_live_trading_cfg)
print(f"DEBUG: Live broker wired (enabled={_live_trading_cfg.get('enabled', False)}, "
      f"dry_run={_live_trading_cfg.get('dry_run', True)})")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run sync and run loop in background
    asyncio.create_task(runtime.run())

    # Start Telegram Command Listener on startup
    from services.chartedge_core.telegram import notifier
    asyncio.create_task(notifier.start_listening(runtime))

    # Background config sync in a thread to avoid blocking the event loop
    if os.getenv("DATABASE_URL"):
        async def background_sync():
            print("DEBUG: Starting background config sync")
            await asyncio.to_thread(sync_config_to_db, config)
            await asyncio.to_thread(apply_db_overrides, config)
            print("DEBUG: Background config sync finished")
        asyncio.create_task(background_sync())

    for _wrapper in positional_runtime_wrappers:
        def _make_positional_loop(wrapper):
            async def positional_loop():
                while True:
                    try:
                        await wrapper.check_once_per_day(runtime)
                    except Exception as e:
                        print(f"⚠️ [Positional/{wrapper.engine.strategy_name}] check failed: {e}")
                    await asyncio.sleep(300)  # every 5 minutes during market hours
            return positional_loop
        asyncio.create_task(_make_positional_loop(_wrapper)())

    if positional_stocks_runtime_wrapper is not None:
        async def positional_stocks_loop():
            while True:
                try:
                    await positional_stocks_runtime_wrapper.check_once_per_day()
                except Exception as e:
                    print(f"⚠️ [Positional Stocks] check failed: {e}")
                await asyncio.sleep(900)  # every 15 min; internal once-per-day guard makes cadence non-critical
        asyncio.create_task(positional_stocks_loop())

    if positional_stocks_midcap_runtime_wrapper is not None:
        async def positional_stocks_midcap_loop():
            while True:
                try:
                    await positional_stocks_midcap_runtime_wrapper.check_once_per_day()
                except Exception as e:
                    print(f"⚠️ [Positional Stocks Midcap] check failed: {e}")
                await asyncio.sleep(900)
        asyncio.create_task(positional_stocks_midcap_loop())

    if positional_stocks_smallcap_runtime_wrapper is not None:
        async def positional_stocks_smallcap_loop():
            while True:
                try:
                    await positional_stocks_smallcap_runtime_wrapper.check_once_per_day()
                except Exception as e:
                    print(f"⚠️ [Positional Stocks Smallcap] check failed: {e}")
                await asyncio.sleep(900)
        asyncio.create_task(positional_stocks_smallcap_loop())

    # NOTE: the daily Upstox WhatsApp/app approval push is event-driven, not
    # a blind daily auto-fire -- positional_stocks_runtime._maybe_request_token()
    # fires it (deduped per day) from inside a pool's analysis run, only at
    # the moment a live-qualifying BUY/SELL is actually found. No signal that
    # day -> no approval ask at all. See upstox_broker.maybe_request_token().

    # Cleanup expired records daily (TTL: 30 days for positional trades, 180 days for stock positions)
    async def cleanup_loop():
        while True:
            try:
                await asyncio.to_thread(cleanup_expired_records)
            except Exception as e:
                print(f"⚠️ Cleanup failed: {e}")
            await asyncio.sleep(86400)  # daily
    asyncio.create_task(cleanup_loop())

    yield

app = FastAPI(title="ChartEdge AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "feed_health": runtime.feed_health, "data_source": data_source}


@app.get("/api/config")
def get_config() -> dict:
    return config.model_dump()


@app.get("/api/signals")
def get_signals() -> list:
    return [s.model_dump(mode="json") for s in runtime.signals]


@app.get("/api/snapshot")
def get_snapshot() -> dict:
    return runtime.snapshot().model_dump(mode="json")


@app.get("/api/positional/status")
def get_positional_status() -> dict:
    """Single-engine shape kept for backward compat (mirrors positional_engines[0]).
    Use /api/positional/status/all for every engine when mode: parallel -- in that
    mode this endpoint only ever reports the first configured strategy, so callers
    get an explicit "note" telling them the other engines exist and are not shown here."""
    if positional_engine is None:
        return {"enabled": False, "open_trade": None, "closed_trades": [], "metrics": {}}
    result = {
        "enabled": True,
        "open_trade": positional_engine.open_trade.to_dict() if positional_engine.open_trade else None,
        "closed_trades": [t.to_dict() for t in positional_engine.closed_trades],
        "metrics": positional_engine.metrics(),
    }
    if len(positional_engines) > 1:
        result["note"] = (
            f"mode: parallel -- {len(positional_engines)} engines active "
            f"({[e.strategy_name for e in positional_engines]}), this endpoint "
            f"only shows '{positional_engine.strategy_name}'. See /api/positional/status/all."
        )
    return result


@app.get("/api/positional/status/all")
def get_positional_status_all() -> dict:
    """One entry per running positional engine, keyed by strategy name.
    Populated for both mode: single (1 entry) and mode: parallel (up to 4)."""
    if not positional_engines:
        return {"enabled": False, "strategies": {}}
    return {
        "enabled": True,
        "strategies": {
            eng.strategy_name: {
                "capital": eng.capital,
                "open_trade": eng.open_trade.to_dict() if eng.open_trade else None,
                "closed_trades": [t.to_dict() for t in eng.closed_trades],
                "metrics": eng.metrics(),
            }
            for eng in positional_engines
        },
    }

@app.post("/api/positional/trigger")
async def trigger_positional(x_trigger_key: Optional[str] = Header(default=None)) -> dict:
    """Manually force one weekly-positional (options) analysis pass per
    configured engine, bypassing the market-hours guard. Meant for manual
    verification (e.g. confirming the Upstox data provider actually resolves
    spot/chain/premiums) instead of waiting on the in-process 5-minute loop.
    Sends the same Telegram alerts as the automatic run on any entry/exit.
    Requires TRIGGER_API_KEY env var to match the X-Trigger-Key header --
    same gating as /api/positional_stocks/trigger, see that endpoint's
    docstring for why (real-money-adjacent via live_trading)."""
    expected_key = os.getenv("TRIGGER_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="TRIGGER_API_KEY not configured on server")
    if x_trigger_key != expected_key:
        raise HTTPException(status_code=403, detail="invalid or missing X-Trigger-Key header")
    if not positional_runtime_wrappers:
        raise HTTPException(status_code=503, detail="positional_risk not enabled in config")
    for wrapper in positional_runtime_wrappers:
        await wrapper.check_once_per_day(runtime, force=True)
    return {
        "ran": True,
        "strategies": [eng.strategy_name for eng in positional_engines],
        "data_source": _positional_data_source,
        "forced": True,
    }


@app.get("/api/positional_stocks/status")
def get_positional_stocks_status() -> dict:
    if positional_stocks_engine is None:
        return {"enabled": False, "open_positions": {}, "closed_positions": [], "metrics": {}}
    return {
        "enabled": True,
        "open_positions": {sym: p.to_dict() for sym, p in positional_stocks_engine.open_positions.items()},
        "closed_positions": [p.to_dict() for p in positional_stocks_engine.closed_positions],
        "metrics": positional_stocks_engine.metrics(),
    }


@app.post("/api/positional_stocks/trigger")
async def trigger_positional_stocks(x_trigger_key: Optional[str] = Header(default=None)) -> dict:
    """Manually force one positional-stocks analysis pass, bypassing the
    once-per-day guard and the post-close cutoff time. Meant for external
    schedulers (e.g. a GitHub Actions cron) to hit instead of relying on the
    in-process daily loop. Sends the same Telegram alerts as the automatic
    run on any entry/exit. Requires TRIGGER_API_KEY env var to match the
    X-Trigger-Key header -- this is real-money-adjacent (positional_stocks_risk
    live_orders may be dry_run/true), so it isn't left open like the other
    unauthenticated status endpoints."""
    expected_key = os.getenv("TRIGGER_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="TRIGGER_API_KEY not configured on server")
    if x_trigger_key != expected_key:
        raise HTTPException(status_code=403, detail="invalid or missing X-Trigger-Key header")
    if positional_stocks_runtime_wrapper is None:
        raise HTTPException(status_code=503, detail="positional_stocks_risk not enabled in config")
    result = await positional_stocks_runtime_wrapper.check_once_per_day(force=True)
    return result


@app.get("/api/positional_stocks_midcap/status")
def get_positional_stocks_midcap_status() -> dict:
    if positional_stocks_midcap_engine is None:
        return {"enabled": False, "open_positions": {}, "closed_positions": [], "metrics": {}}
    return {
        "enabled": True,
        "open_positions": {sym: p.to_dict() for sym, p in positional_stocks_midcap_engine.open_positions.items()},
        "closed_positions": [p.to_dict() for p in positional_stocks_midcap_engine.closed_positions],
        "metrics": positional_stocks_midcap_engine.metrics(),
    }


@app.post("/api/positional_stocks_midcap/trigger")
async def trigger_positional_stocks_midcap(x_trigger_key: Optional[str] = Header(default=None)) -> dict:
    """Manually force one midcap positional-stocks analysis pass. Same
    auth/semantics as /api/positional_stocks/trigger -- see that endpoint's
    docstring."""
    expected_key = os.getenv("TRIGGER_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="TRIGGER_API_KEY not configured on server")
    if x_trigger_key != expected_key:
        raise HTTPException(status_code=403, detail="invalid or missing X-Trigger-Key header")
    if positional_stocks_midcap_runtime_wrapper is None:
        raise HTTPException(status_code=503, detail="positional_stocks_midcap_risk not enabled in config")
    result = await positional_stocks_midcap_runtime_wrapper.check_once_per_day(force=True)
    return result


@app.get("/api/positional_stocks_smallcap/status")
def get_positional_stocks_smallcap_status() -> dict:
    if positional_stocks_smallcap_engine is None:
        return {"enabled": False, "open_positions": {}, "closed_positions": [], "metrics": {}}
    return {
        "enabled": True,
        "open_positions": {sym: p.to_dict() for sym, p in positional_stocks_smallcap_engine.open_positions.items()},
        "closed_positions": [p.to_dict() for p in positional_stocks_smallcap_engine.closed_positions],
        "metrics": positional_stocks_smallcap_engine.metrics(),
    }


@app.post("/api/positional_stocks_smallcap/trigger")
async def trigger_positional_stocks_smallcap(x_trigger_key: Optional[str] = Header(default=None)) -> dict:
    """Manually force one smallcap positional-stocks analysis pass. Same
    auth/semantics as /api/positional_stocks/trigger -- see that endpoint's
    docstring."""
    expected_key = os.getenv("TRIGGER_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="TRIGGER_API_KEY not configured on server")
    if x_trigger_key != expected_key:
        raise HTTPException(status_code=403, detail="invalid or missing X-Trigger-Key header")
    if positional_stocks_smallcap_runtime_wrapper is None:
        raise HTTPException(status_code=503, detail="positional_stocks_smallcap_risk not enabled in config")
    result = await positional_stocks_smallcap_runtime_wrapper.check_once_per_day(force=True)
    return result


@app.post("/api/upstox/token_webhook/{secret}")
async def upstox_token_webhook(secret: str, request: Request) -> dict:
    """Receives the daily Upstox access token after the user approves the
    WhatsApp/in-app push (Upstox posts the token here). Stores it as
    {"date": <today IST>, "access_token": ...} in UPSTOX_TOKEN_FILE, where
    UpstoxBroker.get_valid_token() reads it. Accepts the token under any of
    the common payload keys Upstox/user-proxy may use.

    Secured by a shared secret carried in the URL PATH (Upstox's notifier
    field rejects query strings, and Upstox controls its own POST headers --
    so the secret lives in the path). Register the notifier URL as:
        https://<host>/api/upstox/token_webhook/<UPSTOX_WEBHOOK_SECRET>
    The trailing path segment must equal env UPSTOX_WEBHOOK_SECRET; without
    the env set, the endpoint refuses to store -- so a stray/forged POST
    can't inject a token."""
    import json as _json
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    from pathlib import Path as _Path

    expected = os.getenv("UPSTOX_WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(status_code=503, detail="UPSTOX_WEBHOOK_SECRET not configured on server")
    if secret != expected:
        raise HTTPException(status_code=403, detail="invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    token = (
        payload.get("access_token")
        or payload.get("token")
        or (payload.get("data") or {}).get("access_token")
    )
    if not token:
        raise HTTPException(status_code=400, detail="no access_token in payload")

    today = _dt.now(_ZI("Asia/Kolkata")).strftime("%Y-%m-%d")
    token_path = _Path(os.getenv("UPSTOX_TOKEN_FILE", "data/upstox_token.json"))
    token_path.parent.mkdir(parents=True, exist_ok=True)
    # date drives the daily validity gate in UpstoxBroker.get_valid_token();
    # expires_at (epoch ms from Upstox) kept for reference/observability.
    token_path.write_text(_json.dumps({
        "date": today, "access_token": token,
        "expires_at": payload.get("expires_at"),
        "message_type": payload.get("message_type"),
    }))
    print(f"[Upstox] daily token stored for {today} (expires_at={payload.get('expires_at')})")
    try:
        from services.chartedge_core.telegram import notifier as _n
        await _n.send_message(f"[UPSTOX] daily token received + stored for {today}. Live orders armed for today.")
    except Exception:
        pass
    return {"stored": True, "date": today}


@app.post("/api/upstox/request_token")
async def upstox_request_token(x_trigger_key: Optional[str] = Header(default=None)) -> dict:
    """Manually fire the Upstox access-token-request -> sends the WhatsApp/
    in-app approval push to you. Approve it and the token lands on
    /api/upstox/token_webhook. Same TRIGGER_API_KEY auth as the other
    trigger endpoints. Also fired automatically once/day (see loop below)."""
    expected_key = os.getenv("TRIGGER_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="TRIGGER_API_KEY not configured on server")
    if x_trigger_key != expected_key:
        raise HTTPException(status_code=403, detail="invalid or missing X-Trigger-Key header")
    from services.chartedge_core.upstox_broker import request_access_token
    return request_access_token()


@app.get("/api/upstox/token_status")
def upstox_token_status() -> dict:
    """Non-secret health check: is a valid same-day token present? Lets you
    confirm before 15:35 that the daemon will run live (not fall back to
    paper). Never returns the token itself."""
    from services.chartedge_core.upstox_broker import live_broker
    broker = live_broker()
    tok = broker.get_valid_token()
    return {
        "live_enabled": broker.enabled,
        "dry_run": broker.dry_run,
        "armed": broker.is_armed(),
        "token_present_today": tok is not None,
    }


@app.post("/api/upstox/test_order")
async def upstox_test_order(x_trigger_key: Optional[str] = Header(default=None)) -> dict:
    """One-off manual test of the exact production order path (broker.place_entry),
    independent of whether any positional-stocks signal fired today. Places a
    1-share delivery BUY intent for SBIN through the same funds-check + GTT-stop
    code the daily runtime uses -- with a ₹0/low account, expect a clean
    fail-closed skip (no order, no money moved), which is itself a valid pass:
    it proves the wiring works and money is protected. Does not touch the
    paper DB. TRIGGER_API_KEY-gated, same as the other manual triggers."""
    expected_key = os.getenv("TRIGGER_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="TRIGGER_API_KEY not configured on server")
    if x_trigger_key != expected_key:
        raise HTTPException(status_code=403, detail="invalid or missing X-Trigger-Key header")
    from services.chartedge_core.upstox_broker import live_broker
    broker = live_broker()
    if not broker.enabled:
        raise HTTPException(status_code=503, detail="live_trading not enabled")
    result = broker.place_entry("SBIN", quantity=1, ref_price=800.0, tag="POS_TEST")
    return result.to_dict()


@app.get("/api/debug/option")
def debug_option(symbol: str = "NIFTY", side: str = "BUY") -> dict:
    if symbol not in runtime.candles or not runtime.candles[symbol]:
        return {"error": "symbol_not_found", "symbol": symbol}
        
    spot = runtime.candles[symbol][-1].close
    data = runtime._get_options_data(symbol)
    
    # Also test contract resolution
    structure = runtime.get_multi_leg_structure(symbol, side)
    
    return {
        "symbol": symbol,
        "ltp": spot,
        "pcr": data.pcr if data else 0,
        "resistance_wall": data.resistance_wall if data else 0,
        "support_wall": data.support_wall if data else 0,
        "resolved_structure": structure,
        "chain": data.chain if data else []
    }

@app.get("/api/debug/ltp")
def debug_ltp() -> dict:
    return {
        "tokens": runtime._token_ltp,
        "positions": [
            {
                "instrument": t.instrument,
                "legs": [
                    {"instrument": leg.instrument, "strike": leg.strike, "option_type": leg.option_type}
                    for leg in getattr(t, "legs", [])
                ]
            }
            for t in runtime.trader.open_positions.values()
        ]
    }

@app.get("/api/debug/candles")
def debug_candles() -> dict:
    return {s: len(c) for s, c in runtime.candles.items()}

@app.get("/api/debug/health")
def debug_health() -> dict:
    return {"health": runtime.feed_health}


@app.post("/api/kill-switch")
async def kill_switch() -> dict:
    snapshot = await runtime.kill_switch()
    return snapshot.model_dump(mode="json")


@app.post("/api/config/refresh")
async def refresh_config() -> dict:
    global config
    print("DEBUG: Refreshing config from DB")
    if os.getenv("DATABASE_URL"):
        await asyncio.to_thread(sync_config_to_db, config)
        await asyncio.to_thread(apply_db_overrides, config)
        
    runtime.config = config
    return {"status": "success", "buy_threshold": config.confluence_thresholds.buy_threshold}


@app.post("/api/reset")
def reset() -> dict:
    runtime.reset_runtime_state()
    runtime.feed_health = "RESET"
    return runtime.snapshot().model_dump(mode="json")


# Endpoint to manually trigger historical data re-seeding
@app.post("/api/seed")
async def seed() -> dict:
    if isinstance(runtime, IndstocksMarketRuntime):
        await runtime.seed()
    else:
        runtime.seed()
    return runtime.snapshot().model_dump(mode="json")


@app.post("/api/backtest")
async def run_backtest(target_date: Optional[str] = None) -> dict:
    if target_date:
        today = datetime.strptime(target_date, "%Y-%m-%d").date()
    else:
        today = datetime.now(IST).date()
    start = datetime.combine(today, datetime.min.time(), IST).replace(hour=9, minute=30)
    end = datetime.combine(today, datetime.min.time(), IST).replace(hour=15, minute=5)
    if isinstance(runtime, IndstocksMarketRuntime):
        result = await runtime.run_backtest(start, end)
    else:
        runtime.reset_runtime_state()
        runtime.seed()
        for _ in range(210):
            await runtime.step()
        result = {"status": "ok", "source": "mock"}
    return {"backtest": result, "snapshot": runtime.snapshot().model_dump(mode="json")}


@app.post("/api/simulate/step")
async def simulate_step() -> dict:
    await runtime.step()
    return runtime.snapshot().model_dump(mode="json")


@app.get("/api/history/daily")
def get_daily_history() -> dict:
    from services.chartedge_core.database import get_daily_performance
    return {"history": get_daily_performance()}


@app.post("/api/config/indmoney-token")
def set_indmoney_token(token: str) -> dict:
    from services.chartedge_core.database import set_indmoney_token
    result = set_indmoney_token(token)
    if result:
        return {"status": "ok", "token_id": result.id, "expires_at": result.expires_at}
    return {"status": "error", "message": "Failed to store token"}


@app.get("/api/config/indmoney-token")
def check_indmoney_token() -> dict:
    from services.chartedge_core.database import get_indmoney_token
    token = get_indmoney_token()
    if token:
        return {"status": "ok", "has_token": True}
    return {"status": "ok", "has_token": False}


@app.websocket("/ws")
async def websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            try:
                await websocket.send_json(runtime.snapshot().model_dump(mode="json"))
            except (WebSocketDisconnect, RuntimeError):
                # Clean disconnect, break of the loop
                break
            except Exception as e:
                print(f"WS SNAPSHOT ERROR: {e}")
                break
            await asyncio.sleep(2)
    except Exception as e:
        print(f"WS FATAL ERROR: {e}")
        return


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)

