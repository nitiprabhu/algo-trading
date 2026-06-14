import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from services.chartedge_core.config import load_config
from services.chartedge_core.indstocks import IndstocksMarketRuntime
from services.chartedge_core.regime_agent import AIRegimeAgent

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
DATE_STR = "2026-05-22"

async def main():
    config_path = os.path.abspath("shared/config.yaml")
    config = load_config(config_path)
    
    runtime = IndstocksMarketRuntime(config, skip_db_load=True)
    token = os.getenv("INDMONEY_TOKEN") or os.getenv("INDSTOCKS_TOKEN")
    
    target_date = datetime.strptime(DATE_STR, "%Y-%m-%d")
    start_fetch = target_date - timedelta(days=5)
    
    banknifty_inst = next(i for i in config.instruments if i["symbol"] == "BANKNIFTY")
    vix_inst = next(i for i in config.instruments if i["symbol"] == "INDIAVIX")
    
    print("Fetching BANKNIFTY and INDIAVIX candles...")
    bn_candles = await asyncio.to_thread(runtime._fetch_historical, token, banknifty_inst, start_fetch, target_date)
    vix_candles = await asyncio.to_thread(runtime._fetch_historical, token, vix_inst, start_fetch, target_date)
    
    # Filter previous day
    target_date_utc = target_date.date()
    prev_candles = [c for c in bn_candles if c.time.date() < target_date_utc]
    most_recent_date = max(c.time.date() for c in prev_candles)
    prev_day_candles = [c for c in prev_candles if c.time.date() == most_recent_date]
    
    vix_prev = [c for c in vix_candles if c.time.date() == most_recent_date]
    vix_price = vix_prev[-1].close if vix_prev else 12.5
    
    # Let's get the opening price of BANKNIFTY on target day
    bn_today = sorted([c for c in bn_candles if c.time.date() == target_date_utc], key=lambda x: x.time)
    current_open = bn_today[0].open if bn_today else None
    
    agent = AIRegimeAgent(runtime.signal_engine.provider)
    print(f"\n🤖 Running AIRegimeAgent for BANKNIFTY on {target_date}...")
    decision = await agent.determine_threshold("BANKNIFTY", datetime.combine(target_date, datetime.min.time(), tzinfo=IST), prev_day_candles, vix_price, current_open)
    
    print("\n=======================================================")
    print("BANKNIFTY REGIME DECISION:")
    print(f"Market Regime: {decision.get('market_regime')}")
    print(f"Confluence Threshold: {decision.get('confluence_threshold')}")
    print(f"Volatility Class: {decision.get('volatility_class')}")
    print(f"Reasoning: {decision.get('reasoning')}")
    print("=======================================================")

if __name__ == "__main__":
    asyncio.run(main())
