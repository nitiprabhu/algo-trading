from __future__ import annotations
print("DEBUG: Loading api.py")

import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv

# Load .env before other imports that might use env vars
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from typing import Optional

print("DEBUG: Importing config")
from services.chartedge_core.config import load_config, apply_db_overrides, sync_config_to_db
print("DEBUG: Importing indstocks")
from services.chartedge_core.indstocks import IndstocksMarketRuntime
print("DEBUG: Importing simulation")
from services.chartedge_core.simulation import IST, MarketSimulator


config = load_config()
data_source = os.getenv("CHARTEDGE_DATA_SOURCE", config.data.get("source", "mock"))
print(f"DEBUG: Data source: {data_source}")
print("DEBUG: Initializing runtime...")
runtime = IndstocksMarketRuntime(config) if data_source == "indstocks" else MarketSimulator(config)
print("DEBUG: Runtime initialized")

# Positional (weekly condor) engine -- fully separate from intraday, own capital,
# own JSON log. Only active if shared/config.yaml positional_risk.enabled: true.
positional_engine = None
positional_runtime_wrapper = None
_positional_cfg = config.positional_risk or {}
if _positional_cfg.get("enabled", False):
    from services.chartedge_core.positional_trading import PositionalTradingEngine
    from services.chartedge_core.positional_runtime import PositionalRuntime
    positional_engine = PositionalTradingEngine(
        capital=_positional_cfg.get("capital", 100000.0),
        log_path="data/positional_trades.json",
        strategy_name=_positional_cfg.get("strategy", "condor"),
    )
    positional_runtime_wrapper = PositionalRuntime(positional_engine, _positional_cfg)
    print("DEBUG: Positional trading engine enabled")

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

    if positional_runtime_wrapper is not None:
        async def positional_loop():
            while True:
                try:
                    await positional_runtime_wrapper.check_once_per_day(runtime)
                except Exception as e:
                    print(f"⚠️ [Positional] check failed: {e}")
                await asyncio.sleep(300)  # every 5 minutes during market hours
        asyncio.create_task(positional_loop())

    if positional_stocks_runtime_wrapper is not None:
        async def positional_stocks_loop():
            while True:
                try:
                    await positional_stocks_runtime_wrapper.check_once_per_day()
                except Exception as e:
                    print(f"⚠️ [Positional Stocks] check failed: {e}")
                await asyncio.sleep(900)  # every 15 min; internal once-per-day guard makes cadence non-critical
        asyncio.create_task(positional_stocks_loop())

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
    if positional_engine is None:
        return {"enabled": False, "open_trade": None, "closed_trades": [], "metrics": {}}
    return {
        "enabled": True,
        "open_trade": positional_engine.open_trade.to_dict() if positional_engine.open_trade else None,
        "closed_trades": [t.to_dict() for t in positional_engine.closed_trades],
        "metrics": positional_engine.metrics(),
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

