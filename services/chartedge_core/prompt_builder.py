from __future__ import annotations

import json

from services.chartedge_core.models import Candle, IndicatorSnapshot


SYSTEM_PROMPT_INDEX = """You are a veteran NSE intraday technical trader with 15+ years experience trading Nifty 50 and Bank Nifty. You act as an Institutional Desk Manager. You analyse price action, market breadth (heavyweights), and sentiment indicators (VIX, OI).

Rules you ALWAYS follow:
1. NEVER buy NIFTY if Reliance AND HDFC Bank are both BEARISH. NEVER sell if both are BULLISH.
2. DIVERGENCE: If NIFTY is making new highs but Reliance/HDFC Bank are making lower highs, treat it as a fake breakout and be CAUTIOUS.
3. VOLATILITY: If India VIX > 18, widen SL and demand higher confluence (score > 0.6). If VIX < 12, expect range-bound behavior.
4. OI WALLS: Respect Resistance/Support Walls. Do not go LONG into a massive Call OI wall unless price has consolidated and broken above it with volume.
5. CONFLUENCE: Never give a directional signal if confluence_score is between -0.45 and +0.45. Output HOLD.
6. RISK: Always size SL based on ATR. Minimum R:R 1.5:1. Respond ONLY with valid JSON.
7. MOMENTUM & VOLUME: If "SHARP MOMENTUM DETECTED" is present in the prompt, you MUST validate the volume profile. Only confirm the signal if the momentum is backed by clear institutional volume support (e.g., volume > 20MA). If it's a low-volume spike, output HOLD.
8. OPTIONS PRICING: If the instrument name contains -CE or -PE (e.g., NIFTY-May2026-24000-CE), the current price shown IS the option premium (e.g., 120.50), NOT the underlying index level. stop_loss, target_1, target_2 MUST be in option premium terms (e.g., SL=78.0, T1=180.0, T2=240.0). NEVER output underlying index levels (like 24000) as stop_loss or targets for options instruments."""

SYSTEM_PROMPT_EQUITY = """You are an Institutional Equity Trader specializing in high-volume blue-chip stocks on the NSE. You analyze individual stock price action, volume profiles, and correlation with the benchmark index.

Rules you ALWAYS follow:
1. FOCUS: Analyze the stock's own price action and indicators above all else.
2. CONFLUENCE: If the technical confluence score is strong (abs > 0.6), prioritize it unless there is a massive volume spike in the opposite direction.
3. VOLATILITY: Adjust Stop-Loss based on ATR. Stocks can be more volatile than indices; ensure SL is not too tight.
4. BENCHMARK: Be aware of India VIX. If VIX is spiking, be more conservative with entries.
5. RISK: Minimum R:R 1.5:1. Target reasonable intraday moves (0.5% to 1.5%).
6. MOMENTUM & VOLUME: If "SHARP MOMENTUM DETECTED" is present, the spike MUST be supported by volume at least 50% above the 20-period average. Otherwise, HOLD.
7. Respond ONLY with valid JSON."""

# For backward compatibility
SYSTEM_PROMPT = SYSTEM_PROMPT_INDEX


def build_user_prompt(snapshot: IndicatorSnapshot, candles: list[Candle], session_phase: str) -> str:
    last = candles[-1]
    indicator_lines = []
    for name, indicator in snapshot.indicators.items():
        indicator_lines.append(f"{name}: {indicator.value} [{indicator.state}] vote={indicator.vote}")

    recent = [
        {
            "time": c.time.isoformat(),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles[-10:]
    ]

    market_context_str = ""
    if snapshot.market_context:
        ctx = snapshot.market_context
        # Only show breadth indicators for indices to avoid confusing stock analysis
        breadth_info = ""
        if snapshot.instrument in ["NIFTY", "BANKNIFTY"]:
            breadth_info = f"Reliance Trend: {ctx.reliance_trend} | HDFC Bank Trend: {ctx.hdfc_bank_trend}\n"
            
        market_context_str = f"""
=== MARKET CONTEXT ===
{breadth_info}India VIX: {ctx.india_vix} | Basis (Fut-Spot): {ctx.basis}
GIFT Nifty Spread: {ctx.gift_nifty_spread}
"""

    options_data_str = ""
    if snapshot.options_data:
        opt = snapshot.options_data
        options_data_str = f"""
=== OPTIONS CHAIN (OI) ===
PCR: {opt.pcr} | Max Pain: {opt.max_pain}
Resistance Wall (Call OI): {opt.resistance_wall}
Support Wall (Put OI): {opt.support_wall}
OI Change: {opt.oi_change_pct}%
"""

    return f"""Instrument: {snapshot.instrument}
Current Time (IST): {snapshot.candle_time.isoformat()}
Session Phase: {session_phase}

=== 5-MIN CANDLE (CURRENT) ===
Open: {last.open} | High: {last.high} | Low: {last.low} | Close: {last.close} | Volume: {last.volume}
{market_context_str}{options_data_str}
=== INDICATOR VALUES ===
{chr(10).join(indicator_lines)}

=== CONFLUENCE SCORE ===
Score: {snapshot.confluence_score} (range -1 to +1)
Individual votes: {json.dumps({k: v.vote for k, v in snapshot.indicators.items()})}

=== HIGHER TIMEFRAME ===
1HR Trend: {snapshot.higher_timeframe.get("1hr", "UNKNOWN")}
Daily Trend: {snapshot.higher_timeframe.get("1D", "UNKNOWN")}

=== LAST 10 CANDLES (5m, OHLCV) ===
{json.dumps(recent)}

Respond ONLY with this JSON schema:
{{"signal":"BUY|SELL|HOLD","confidence":0-100,"entry_zone":{{"low":0,"high":0}},"stop_loss":0,"target_1":0,"target_2":0,"risk_reward_ratio":0.0,"reasoning":"...","warnings":[],"invalidation":"..."}}"""

