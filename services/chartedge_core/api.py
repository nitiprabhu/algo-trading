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
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run sync and run loop in background
    asyncio.create_task(runtime.run())
    
    # Initialize Telegram Chat ID resolution on startup
    from services.chartedge_core.telegram import notifier
    asyncio.create_task(notifier.resolve_chat_id())
    
    # Background config sync in a thread to avoid blocking the event loop
    if os.getenv("DATABASE_URL"):
        async def background_sync():
            print("DEBUG: Starting background config sync")
            await asyncio.to_thread(sync_config_to_db, config)
            await asyncio.to_thread(apply_db_overrides, config)
            print("DEBUG: Background config sync finished")
        asyncio.create_task(background_sync())
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

