"""AIRegimeAgent — classifies prior-day market regime + derives dynamic confluence threshold.

Uses AI with a deterministic rule-based fallback so the system works even when
the LLM is unavailable.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from services.chartedge_core.models import Candle

REGIME_TRENDING_BULLISH = "TRENDING_BULLISH"
REGIME_TRENDING_BEARISH = "TRENDING_BEARISH"
REGIME_RANGE_BOUND_CHOP = "RANGE_BOUND_CHOP"
REGIME_MEAN_REVERTING = "MEAN_REVERTING"

_SYSTEM_PROMPT = """You are an expert NSE quant analyst. Given previous-day 1-minute OHLCV candles
for an index, classify the market regime and set ALL trading parameters for the upcoming session.
Output valid JSON only — no commentary outside the JSON block.

Regimes:
- TRENDING_BULLISH: clear sustained upward move with follow-through
- TRENDING_BEARISH: clear sustained downward move with follow-through
- RANGE_BOUND_CHOP: no clear direction, price oscillates within a band
- MEAN_REVERTING: sharp directional move rejected; price reverts toward open

JSON schema (no extra keys):
{
  "market_regime": "<one of the four regimes above>",
  "confluence_threshold": <float 0.45–0.60>,
  "volatility_class": "LOW" | "NORMAL" | "HIGH",
  "indicator_weights": {
    "rsi": <float>, "macd": <float>, "ema_ribbon": <float>,
    "vwap": <float>, "supertrend": <float>, "volume": <float>
  },
  "sl_atr_multiplier": <float 1.0–2.0>,
  "options_bias": "CE" | "PE" | "NEUTRAL",
  "optimal_strategy": "DEBIT_SPREAD" | "RATIO_BACKSPREAD" | "IRON_CONDOR" | "SHORT_STRANGLE" | "CREDIT_SPREAD" | "NAKED_BUY",
  "theta_timeout_mins": <int 30–90>,
  "avoid_first_30_mins": <true|false>,
  "reasoning": "<1-2 sentences>",
  "key_observations": ["<observation 1>", "<observation 2>"]
}

Parameter guidance:

confluence_threshold:
- TRENDING: 0.45–0.50 (clean trend signals at lower threshold)
- RANGE_BOUND_CHOP: 0.50–0.55 (mean-reversion works; don't set above 0.55)
- MEAN_REVERTING: 0.50–0.54
- Low VIX (<13): subtract 0.02. High VIX (>20): add 0.03.

indicator_weights (must sum to exactly 1.0):
- TRENDING_BULLISH/BEARISH: boost macd(0.22) + supertrend(0.26) + ema_ribbon(0.16), reduce rsi(0.12) + vwap(0.14) + volume(0.10)
- RANGE_BOUND_CHOP: boost rsi(0.22) + vwap(0.24), reduce supertrend(0.14) + macd(0.14) + ema_ribbon(0.12) + volume(0.14)
- MEAN_REVERTING: boost rsi(0.24) + vwap(0.22) + macd(0.18), reduce ema_ribbon(0.12) + supertrend(0.14) + volume(0.10)

sl_atr_multiplier:
- LOW volatility: 1.0–1.2 (tight SL, market not moving much)
- NORMAL: 1.2–1.5
- HIGH volatility: 1.5–2.0 (wide SL to avoid premature stop-outs)

options_bias (directional lean for the upcoming session):
- TRENDING_BULLISH + gap-up > 0.3%: "CE"
- TRENDING_BEARISH + gap-down > 0.3%: "PE"
- RANGE_BOUND_CHOP or unclear gap: "NEUTRAL"
- MEAN_REVERTING: opposite of prior-day final direction

optimal_strategy:
- TRENDING (Normal VIX): "DEBIT_SPREAD"
- TRENDING (Low VIX < 13): "RATIO_BACKSPREAD"
- RANGE_BOUND_CHOP (Normal/High VIX): "IRON_CONDOR" or "SHORT_STRANGLE"
- MEAN_REVERTING: "CREDIT_SPREAD"
- Unclear context: "NAKED_BUY"

theta_timeout_mins (how long to hold option before theta mitigation):
- TRENDING: 75–90 (let winners run on trend days)
- RANGE_BOUND_CHOP: 30–45 (exit early if no momentum)
- MEAN_REVERTING: 45–60

avoid_first_30_mins:
- true if: gap > 0.5% either direction, or prior day had high tail wicks at open, or VIX > 18
- false otherwise (normal open, low VIX)

Global context interpretation (when provided):
- sp500_pct / nasdaq_pct: US market % change from prior close
  * Strong US rally (>+1%): slight CE bias for NIFTY/BANKNIFTY open; may lower threshold by 0.01
  * Strong US selloff (<-1%): slight PE bias; may raise threshold by 0.01; set avoid_first_30_mins=true
  * Flat US (between -0.5% and +0.5%): no adjustment
- fii_cash_net_cr: FII net cash flow in ₹ crore (positive = buying, negative = selling)
  * Strong FII buying (>+1000 cr): supports CE bias on trending days
  * Strong FII selling (<-1000 cr): supports PE bias; more caution on entries
  * Near zero: neutral, no adjustment
- fii_deriv_net_cr: FII net derivatives flow — less directional signal, more hedging proxy
  * Large positive: FIIs adding longs; bullish lean
  * Large negative: FIIs adding shorts/hedges; cautious lean

upcoming_events (list of event names within 1 day of session):
- Any RBI MPC Rate Decision: raise threshold +0.03, set avoid_first_30_mins=true, widen SL mult +0.2
- Any US FOMC Rate Decision: raise threshold +0.02, set avoid_first_30_mins=true
- India Union Budget: raise threshold +0.04, set avoid_first_30_mins=true, options_bias=NEUTRAL
- US CPI Inflation Data: raise threshold +0.02 if on same day; neutral if day after
- Multiple events on same day: cap total threshold raise at +0.05; always set avoid_first_30_mins=true
"""


def _classify_rule_based(candles: list[Candle], vix: float) -> dict[str, Any]:
    """Deterministic fallback when AI call fails or is unavailable."""
    if not candles:
        return {
            "market_regime": REGIME_RANGE_BOUND_CHOP,
            "confluence_threshold": 0.52,
            "volatility_class": "NORMAL",
            "indicator_weights": {"rsi": 0.22, "macd": 0.14, "ema_ribbon": 0.12, "vwap": 0.24, "supertrend": 0.14, "volume": 0.14},
            "sl_atr_multiplier": 1.3,
            "options_bias": "NEUTRAL",
            "optimal_strategy": "IRON_CONDOR",
            "theta_timeout_mins": 45,
            "avoid_first_30_mins": False,
            "reasoning": "No candle data — defaulting to chop regime.",
            "key_observations": [],
        }

    prices = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    day_open = candles[0].open
    day_close = prices[-1]
    day_high = max(highs)
    day_low = min(lows)
    day_range = day_high - day_low or 1.0
    net_move = day_close - day_open
    directional_ratio = abs(net_move) / day_range

    reversals = sum(
        1
        for i in range(2, len(prices))
        if (prices[i] - prices[i - 1]) * (prices[i - 1] - prices[i - 2]) < 0
    )
    reversal_ratio = reversals / max(len(prices) - 2, 1)

    vol_class = "HIGH" if vix > 20 else ("LOW" if vix < 13 else "NORMAL")

    if directional_ratio >= 0.50 and reversal_ratio < 0.45:
        regime = REGIME_TRENDING_BULLISH if net_move > 0 else REGIME_TRENDING_BEARISH
        threshold = 0.48
        weights = {"rsi": 0.12, "macd": 0.22, "ema_ribbon": 0.16, "vwap": 0.14, "supertrend": 0.26, "volume": 0.10}
        sl_mult = 1.2
        options_bias = "CE" if net_move > 0 else "PE"
        optimal_strategy = "RATIO_BACKSPREAD" if vol_class == "LOW" else "DEBIT_SPREAD"
        theta = 75
        skip_open = False
    elif reversal_ratio > 0.55 or directional_ratio < 0.25:
        regime = REGIME_RANGE_BOUND_CHOP
        threshold = 0.52
        weights = {"rsi": 0.22, "macd": 0.14, "ema_ribbon": 0.12, "vwap": 0.24, "supertrend": 0.14, "volume": 0.14}
        sl_mult = 1.3
        options_bias = "NEUTRAL"
        optimal_strategy = "IRON_CONDOR"
        theta = 40
        skip_open = False
    elif directional_ratio >= 0.35 and reversal_ratio >= 0.45:
        regime = REGIME_MEAN_REVERTING
        threshold = 0.51
        weights = {"rsi": 0.24, "macd": 0.18, "ema_ribbon": 0.12, "vwap": 0.22, "supertrend": 0.14, "volume": 0.10}
        sl_mult = 1.4
        options_bias = "CE" if net_move < 0 else "PE"  # fade prior-day direction
        optimal_strategy = "CREDIT_SPREAD"
        theta = 50
        skip_open = False
    else:
        regime = REGIME_RANGE_BOUND_CHOP
        threshold = 0.52
        weights = {"rsi": 0.22, "macd": 0.14, "ema_ribbon": 0.12, "vwap": 0.24, "supertrend": 0.14, "volume": 0.14}
        sl_mult = 1.3
        options_bias = "NEUTRAL"
        optimal_strategy = "IRON_CONDOR"
        theta = 40
        skip_open = False

    if vol_class == "HIGH":
        threshold = min(threshold + 0.03, 0.60)
        sl_mult = min(sl_mult + 0.3, 2.0)
        skip_open = True
    elif vol_class == "LOW":
        threshold = max(threshold - 0.02, 0.43)
        sl_mult = max(sl_mult - 0.1, 1.0)

    # VIX > 17 = avoid first 30 mins regardless of regime (opening traps common at elevated VIX)
    if vix > 17:
        skip_open = True

    return {
        "market_regime": regime,
        "confluence_threshold": round(threshold, 2),
        "volatility_class": vol_class,
        "indicator_weights": weights,
        "sl_atr_multiplier": round(sl_mult, 2),
        "options_bias": options_bias,
        "optimal_strategy": optimal_strategy,
        "theta_timeout_mins": theta,
        "avoid_first_30_mins": skip_open,
        "reasoning": (
            f"Directional ratio {directional_ratio:.2f}, reversal ratio {reversal_ratio:.2f}, "
            f"VIX {vix:.1f}."
        ),
        "key_observations": [
            f"Day range: {day_low:.1f}–{day_high:.1f} ({day_range:.1f} pts)",
            f"Net move: {net_move:+.1f} pts ({net_move / day_open * 100:.2f}%)",
        ],
    }


class AIRegimeAgent:
    """
    Classifies prior-day market regime and derives a dynamic confluence
    threshold for the upcoming session.

    Interface:
        agent = AIRegimeAgent(provider)
        result = await agent.determine_threshold(symbol, target_date, candles, vix, current_open)
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    async def determine_threshold(
        self,
        symbol: str,
        target_date: datetime,
        prev_day_candles: list[Candle],
        vix_price: float,
        current_open: float | None = None,
        global_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fallback = _classify_rule_based(prev_day_candles, vix_price)
        try:
            prompt = self._build_prompt(symbol, target_date, prev_day_candles, vix_price, current_open, global_context)
            raw = await self.provider.complete(prompt, _SYSTEM_PROMPT)
            result = self._parse(raw)
            if result:
                return result
        except Exception as e:
            print(f"⚠️ AIRegimeAgent AI call failed for {symbol}: {e}. Using rule-based classification.")
        return fallback

    def _build_prompt(
        self,
        symbol: str,
        target_date: datetime,
        candles: list[Candle],
        vix: float,
        current_open: float | None,
        global_context: dict[str, Any] | None = None,
    ) -> str:
        if not candles:
            return f"No candle data for {symbol}. Classify as RANGE_BOUND_CHOP."

        prices = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        header = (
            f"Symbol: {symbol} | Target session: {target_date.date()}\n"
            f"Previous day | Open: {candles[0].open:.2f}  Close: {prices[-1]:.2f}  "
            f"High: {max(highs):.2f}  Low: {min(lows):.2f}\n"
            f"India VIX: {vix:.2f}\n"
        )
        if current_open:
            header += f"Today's gap-open indication: {current_open:.2f}\n"

        global_ctx_str = ""
        if global_context:
            sp = global_context.get("sp500_pct")
            nq = global_context.get("nasdaq_pct")
            fii_cash = global_context.get("fii_cash_net_cr")
            fii_deriv = global_context.get("fii_deriv_net_cr")
            events = global_context.get("upcoming_events", [])
            lines = []
            if sp is not None:
                lines.append(f"S&P 500 prev close: {sp:+.2f}%")
            if nq is not None:
                lines.append(f"Nasdaq prev close: {nq:+.2f}%")
            if fii_cash is not None:
                lines.append(f"FII cash net: ₹{fii_cash:+,.0f} cr")
            if fii_deriv is not None:
                lines.append(f"FII derivatives net: ₹{fii_deriv:+,.0f} cr")
            if events:
                lines.append(f"⚠️ HIGH-IMPACT EVENTS NEARBY: {', '.join(events)}")
            dow = global_context.get("dow_pct")
            if dow is not None:
                lines.append(f"Dow prev close: {dow:+.2f}%")
            gift_pts = global_context.get("gift_nifty_pts")
            gift_pct = global_context.get("gift_nifty_pct")
            if gift_pts is not None:
                lines.append(f"GIFT Nifty gap: {gift_pts:+.0f} pts ({gift_pct:+.2f}%)")
            earnings = global_context.get("us_earnings_nearby", [])
            if earnings:
                lines.append(f"US Earnings nearby: {', '.join(earnings)}")
            india_news = global_context.get("india_news", [])
            if india_news:
                lines.append(f"India News: {' | '.join(india_news[:3])}")
            global_news = global_context.get("global_news", [])
            if global_news:
                lines.append(f"Global/Geo News: {' | '.join(global_news[:3])}")
            if lines:
                global_ctx_str = "\n=== GLOBAL CONTEXT ===\n" + "\n".join(lines) + "\n"

        step = max(1, len(candles) // 20)
        rows = "\n".join(
            f"  {c.time.strftime('%H:%M')} O={c.open:.1f} H={c.high:.1f} "
            f"L={c.low:.1f} C={c.close:.1f} V={c.volume}"
            for c in candles[::step][:20]
        )
        return f"{header}{global_ctx_str}\nSampled candles (every {step} min):\n{rows}"

    def _parse(self, raw: str) -> dict[str, Any] | None:
        cleaned = raw.strip()
        if "```" in cleaned:
            for part in cleaned.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    cleaned = part[4:].strip()
                    break
                if part.startswith("{"):
                    cleaned = part
                    break
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            data = json.loads(cleaned[start: end + 1])
            required = {"market_regime", "confluence_threshold", "volatility_class", "reasoning"}
            if not required.issubset(data):
                return None
            data["confluence_threshold"] = max(0.40, min(float(data["confluence_threshold"]), 0.60))
            data.setdefault("key_observations", [])
            data.setdefault("sl_atr_multiplier", 1.3)
            data.setdefault("options_bias", "NEUTRAL")
            data.setdefault("optimal_strategy", "NAKED_BUY")
            data.setdefault("theta_timeout_mins", 45)
            data.setdefault("avoid_first_30_mins", False)
            # Validate + normalise indicator weights if present
            w = data.get("indicator_weights")
            expected_keys = {"rsi", "macd", "ema_ribbon", "vwap", "supertrend", "volume"}
            if isinstance(w, dict) and expected_keys.issubset(w):
                total = sum(float(v) for v in w.values())
                if total > 0:
                    data["indicator_weights"] = {k: round(float(v) / total, 4) for k, v in w.items()}
            else:
                data["indicator_weights"] = None  # signal caller to use config default
            return data
        except Exception:
            return None
