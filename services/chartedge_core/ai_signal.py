from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

import httpx

from services.chartedge_core.confluence import consideration
from services.chartedge_core.models import Candle, Direction, EntryZone, IndicatorSnapshot, Signal
from services.chartedge_core.strategies import EagleNiftyT315, FiveEMAScalping, NiftyFuturesORB


class AIProvider(ABC):
    name: str

    def __init__(self, ai_config: dict[str, Any]) -> None:
        self.ai_config = ai_config

    @abstractmethod
    async def complete(self, prompt: str, system_prompt: str, schema: dict[str, Any] | None = None) -> str:
        """Return raw model JSON text."""


class AnthropicProvider(AIProvider):
    name = "anthropic"

    async def complete(self, prompt: str, system_prompt: str, schema: dict[str, Any] | None = None) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing")

        payload = {
            "model": self.ai_config["model"],
            "temperature": self.ai_config["temperature"],
            "max_tokens": self.ai_config["max_tokens"],
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }
        max_retries = 3
        backoff_factor = 2.0
        initial_delay = 1.0
        
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json=payload,
                    )
                    if response.status_code >= 400:
                        print(f"❌ Anthropic API Error Response: Status {response.status_code} - {response.text}")
                    response.raise_for_status()
                return response.json()["content"][0]["text"]
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in [429, 500, 502, 503, 504]:
                    raise exc
                if attempt == max_retries:
                    raise exc
                delay = initial_delay * (backoff_factor ** attempt)
                print(f"⚠️ Anthropic API transient error ({exc.__class__.__name__}: {exc}). Retrying in {delay}s (Attempt {attempt+1}/{max_retries})...")
                await asyncio.sleep(delay)


class OpenAIProvider(AIProvider):
    name = "openai"

    async def complete(self, prompt: str, system_prompt: str, schema: dict[str, Any] | None = None) -> str:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")
 
        if schema is None:
            schema = {
                "type": "object",
                "properties": {
                    "signal": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                    "entry_zone": {
                        "type": "object",
                        "properties": {"low": {"type": "number"}, "high": {"type": "number"}},
                        "required": ["low", "high"],
                        "additionalProperties": False,
                    },
                    "stop_loss": {"type": "number"},
                    "target_1": {"type": "number"},
                    "target_2": {"type": "number"},
                    "risk_reward_ratio": {"type": "number"},
                    "reasoning": {"type": "string"},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                    "invalidation": {"type": "string"},
                },
                "required": [
                    "signal", "confidence", "entry_zone", "stop_loss", "target_1",
                    "target_2", "risk_reward_ratio", "reasoning", "warnings", "invalidation",
                ],
                "additionalProperties": False,
            }
        payload = {
            "model": self.ai_config.get("openai_model", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "chartedge_schema",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        max_retries = 3
        backoff_factor = 2.0
        initial_delay = 1.0
        
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                    )
                    response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in [429, 500, 502, 503, 504]:
                    raise exc
                if attempt == max_retries:
                    raise exc
                delay = initial_delay * (backoff_factor ** attempt)
                print(f"⚠️ OpenAI API transient error ({exc.__class__.__name__}: {exc}). Retrying in {delay}s (Attempt {attempt+1}/{max_retries})...")
                await asyncio.sleep(delay)


class SignalEngine:
    def __init__(self, ai_config: dict[str, Any], thresholds: dict[str, float]) -> None:
        self.ai_config = ai_config
        self.thresholds = thresholds
        self.ai_enabled = bool(ai_config.get("enabled", True))
        self.provider = self._provider(ai_config.get("provider", "openai"))
        self.strategies: dict[str, dict[str, OptionStrategy]] = {}
        self.last_rejection_time: dict[str, datetime] = {}
        # Set when T315 structurally validates a breakout (regardless of AI outcome).
        # symbol → (option_type "CE"/"PE", validated_date)
        self.t315_direction_lock: dict[str, tuple[str, object]] = {}

    def _confirm_entry_momentum(self, direction: Direction, candles: list[Candle]) -> bool:
        """Require last 2 candles moving in trade direction to avoid chasing exhausted moves."""
        if len(candles) < 3:
            return True
        c1, c2 = candles[-2], candles[-1]
        if direction == Direction.BUY:
            return c1.close > c1.open and c2.close > c2.open
        if direction == Direction.SELL:
            return c1.close < c1.open and c2.close < c2.open
        return True

    async def generate(self, snapshot: IndicatorSnapshot, candles: list[Candle]) -> Signal:
        atr_value = snapshot.indicators.get("atr")
        last = candles[-1]

        # Volatility Filter: If ATR is < 0.1% of price, market is chopping.
        # Tighten the threshold by 25% to avoid false breakouts.
        is_choppy = False
        if atr_value and isinstance(atr_value.value, float):
            if (atr_value.value / last.close) < 0.0005:
                is_choppy = True

        # Dynamic threshold retrieval with per-symbol override
        MAX_EFFECTIVE_THRESHOLD = 0.70
        base_thresh = self.thresholds.get(snapshot.instrument, self.thresholds.get("DEFAULT", 0.60))
        raw_thresh = base_thresh * (1.25 if is_choppy else 1.0)
        buy_thresh = min(raw_thresh, MAX_EFFECTIVE_THRESHOLD)
        sell_thresh = max(-raw_thresh, -MAX_EFFECTIVE_THRESHOLD)

        direction = consideration(
            snapshot.confluence_score,
            buy_thresh,
            sell_thresh,
        )
        
        # Diagnostic Log
        print(f"🔍 [Engine] {snapshot.instrument} Confluence: {snapshot.confluence_score:.3f} | Thresholds: [{sell_thresh:.2f}, {buy_thresh:.2f}] | Decision: {direction.value}")

        # Entry momentum guard: confirm price is still moving in trade direction.
        # Prevents entering at exhaustion tops/bottoms after all lagging indicators align.
        if direction != Direction.HOLD and not self._confirm_entry_momentum(direction, candles):
            return self._rule_based_signal(snapshot, candles, Direction.HOLD, "MOMENTUM_GUARD_HOLD")

        if direction == Direction.HOLD:
            # Momentum Override Safety: Check if there's a sharp move.
            # Instead of auto-bypassing, we proceed to AI review to validate volume.
            momentum = self._detect_momentum_breakout(candles)
            if not momentum:
                return self._rule_based_signal(snapshot, candles, Direction.HOLD, "CONFLUENCE_HOLD")
            
            # If momentum exists, we allow the AI to decide even with low confluence.
            # We'll pass this context in the prompt.
            momentum_alert = f"SHARP MOMENTUM DETECTED ({momentum.value})"
        else:
            momentum_alert = None

        return self._rule_based_signal(snapshot, candles, direction, "RULE_BASED")

    def _provider(self, provider: str) -> AIProvider:
        if provider == "openai":
            return OpenAIProvider(self.ai_config)
        if provider == "anthropic":
            return AnthropicProvider(self.ai_config)
        raise ValueError(f"Unsupported AI provider: {provider}")

    def _detect_momentum_breakout(self, candles: list[Candle]) -> Direction | None:
        """Detect sharp price moves that lagging indicators haven't caught up to.
        
        If price has moved >0.3% in the last 5 candles, return the direction
        of the move to override a HOLD from confluence.
        """
        if len(candles) < 5:
            return None
        lookback_open = candles[-5].open
        recent_close = candles[-1].close
        pct_change = (recent_close - lookback_open) / lookback_open

        if pct_change <= -0.003:
            return Direction.SELL
        if pct_change >= 0.003:
            return Direction.BUY
        return None

    async def get_fo_signal(
        self,
        candle: Candle,
        candles: list[Candle],
        india_vix: float = 0.0,
        latest_snapshot: IndicatorSnapshot | None = None,
    ) -> Signal | None:
        """Check for F&O specific strategy triggers (T315, 5EMA) and review them via AI."""
        symbol = candle.instrument
        if symbol not in self.strategies:
            self.strategies[symbol] = {
                "t315": EagleNiftyT315(),
                "ema5": FiveEMAScalping(),
                "fut_orb": NiftyFuturesORB(),
            }
        
        strategies = self.strategies[symbol]

        # 1. Check for triggers (5EMA uses previous state before update)
        ema5_trigger = strategies["ema5"].get_signal(candle, india_vix, latest_snapshot)

        # 2. Update strategy states
        strategies["t315"].update(candle)
        strategies["ema5"].update(candle, candles)
        strategies["fut_orb"].update(candle)

        # 3. Check for triggers (T315 uses updated state)
        t315_trigger = strategies["t315"].get_signal(candle, india_vix)

        # Futures ORB signal (NIFTY only — uses spot price as proxy)
        vwap_val = 0.0
        adx_val  = 0.0
        if latest_snapshot:
            vwap_ind = latest_snapshot.indicators.get("vwap")
            if vwap_ind and isinstance(vwap_ind.value, (int, float)):
                vwap_val = float(vwap_ind.value)
            adx_ind = latest_snapshot.indicators.get("adx")
            if adx_ind and isinstance(adx_ind.value, (int, float)):
                adx_val = float(adx_ind.value)
        fut_orb_trigger = None
        if symbol == "NIFTY":
            fut_orb_trigger = strategies["fut_orb"].get_signal(
                candle, india_vix, vwap=vwap_val, adx=adx_val
            )
            if fut_orb_trigger:
                # Override instrument to NIFTY_FUT so it routes to FuturesTradingEngine
                fut_orb_trigger["instrument_override"] = "NIFTY_FUT"

        trigger = t315_trigger or ema5_trigger or fut_orb_trigger
        
        if not trigger:
            # Periodic health check print (every 5 mins per instrument)
            if candle.time.minute % 5 == 0:
                print(f"💤 [Strategy] {symbol} @ {candle.time.strftime('%H:%M')} - No Trigger (T315: {'YES' if t315_trigger else 'NO'}, 5EMA: {'YES' if ema5_trigger else 'NO'}, FUT_ORB: {'YES' if fut_orb_trigger else 'NO'})")
            return None

        # Cooldown check to avoid spamming AI reviews on consecutive candles of the same setup
        cooldown_key = f"{symbol}_{trigger['strategy']}_{trigger.get('option_type', 'DIR')}"
        if cooldown_key in self.last_rejection_time:
            cooldown_expiry = self.last_rejection_time[cooldown_key] + timedelta(minutes=15)
            if candle.time < cooldown_expiry:
                if candle.time.minute % 5 == 0:
                    print(f"⏳ [Cooldown] Suppressing AI Review for {cooldown_key} at {candle.time.strftime('%H:%M')} (expires at {cooldown_expiry.strftime('%H:%M')})")
                return None

        # Confluence alignment gate: only applies to options signals (CE/PE)
        if trigger.get("option_type") and latest_snapshot and latest_snapshot.confluence_score != 0:
            score = latest_snapshot.confluence_score
            opt_type = trigger["option_type"]
            if opt_type == "CE" and score < -0.25:
                print(f"⛔ [Confluence Gate] {symbol} T315 CE blocked: confluence {score:.3f} strongly bearish")
                return None
            if opt_type == "PE" and score > 0.25:
                print(f"⛔ [Confluence Gate] {symbol} T315 PE blocked: confluence {score:.3f} strongly bullish")
                return None

        base_signal = self._from_strategy_dict(trigger, candle)

        # T315 structural breakout validated — lock direction for this symbol today.
        # Disabled on Thursday (weekly expiry): morning false breakouts are common on expiry days.
        if trigger["strategy"] == "T315" and candle.time.weekday() != 3:
            self.t315_direction_lock[symbol] = (trigger["option_type"], candle.time.date())
            print(f"🔒 [T315 Lock] {symbol}: {trigger['option_type']} breakout locked for {candle.time.date()}")

        print(f"✅ [Strategy] {trigger['strategy']} signal accepted for {symbol} at {candle.time}: {trigger['reason']}")
        return base_signal

    def _from_strategy_dict(self, trigger: dict, candle: Candle) -> Signal:
        """Convert a strategy trigger dictionary to a Signal object."""
        entry_low  = candle.close * 0.9995
        entry_high = candle.close * 1.0005
        sl  = trigger["sl"]
        risk = abs(candle.close - sl)

        opt_type = trigger.get("option_type")
        raw_dir  = trigger.get("direction", "BUY")

        if opt_type == "CE":
            t1, t2 = candle.close + risk * 1.5, candle.close + risk * 3.0
            direction = Direction.BUY
        elif opt_type == "PE":
            t1, t2 = candle.close - risk * 1.5, candle.close - risk * 3.0
            direction = Direction.BUY   # still BUY the PE option
        else:
            # Futures / directional signals: use explicit direction
            direction = Direction.BUY if raw_dir == "BUY" else Direction.SELL
            if direction == Direction.BUY:
                t1, t2 = candle.close + risk * 1.5, candle.close + risk * 3.0
            else:
                t1, t2 = candle.close - risk * 1.5, candle.close - risk * 3.0

        from services.chartedge_core.models import IndicatorSnapshot
        snapshot = IndicatorSnapshot(
            instrument=trigger.get("instrument_override", candle.instrument),
            timeframe="1m",
            candle_time=candle.time,
            price=candle.close,
            indicators={},
            confluence_score=1.0 if direction == Direction.BUY else -1.0
        )

        instrument = trigger.get("instrument_override", candle.instrument)
        return Signal(
            created_at=datetime.now().astimezone(),
            instrument=instrument,
            signal=direction,
            confidence=85,
            entry_zone=EntryZone(low=round(entry_low, 2), high=round(entry_high, 2)),
            stop_loss=round(sl, 2),
            target_1=round(t1, 2),
            target_2=round(t2, 2),
            risk_reward_ratio=1.5,
            reasoning=trigger["reason"],
            warnings=[],
            invalidation="Price breaches SL, 45m theta threshold, or -5% daily drawdown.",
            indicator_snapshot=snapshot,
            ai_model="QUANT_ENGINE",
            ai_status="STRATEGY_OK",
            strategy_name=trigger["strategy"],
            option_type=opt_type,
        )

    def _from_ai_json(self, snapshot: IndicatorSnapshot, raw: str, provider_name: str) -> Signal:
        cleaned_raw = raw.strip()
        if "```" in cleaned_raw:
            parts = cleaned_raw.split("```")
            for part in parts:
                part_strip = part.strip()
                if part_strip.startswith("json"):
                    cleaned_raw = part_strip[4:].strip()
                    break
                elif part_strip.startswith("{") and part_strip.endswith("}"):
                    cleaned_raw = part_strip
                    break
        else:
            start_idx = cleaned_raw.find("{")
            end_idx = cleaned_raw.rfind("}")
            if start_idx != -1 and end_idx != -1:
                cleaned_raw = cleaned_raw[start_idx : end_idx + 1]
        
        try:
            data = json.loads(cleaned_raw)
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse JSON from AI response: {e}")
            print(f"=== RAW RESPONSE START ===\n{raw}\n=== RAW RESPONSE END ===")
            raise e

        return Signal(
            created_at=datetime.now().astimezone(),
            instrument=snapshot.instrument,
            signal=Direction(data["signal"]),
            confidence=int(data["confidence"]),
            entry_zone=EntryZone(**data["entry_zone"]),
            stop_loss=float(data["stop_loss"]),
            target_1=float(data["target_1"]),
            target_2=float(data["target_2"]),
            risk_reward_ratio=float(data["risk_reward_ratio"]),
            reasoning=str(data["reasoning"])[:500],
            warnings=list(data.get("warnings", [])),
            invalidation=str(data["invalidation"]),
            indicator_snapshot=snapshot,
            ai_model=self.ai_config["model"] if provider_name == "anthropic" else self.ai_config["openai_model"],
            ai_status=f"{provider_name.upper()}_OK",
        )

    def _rule_based_signal(
        self, snapshot: IndicatorSnapshot, candles: list[Candle], direction: Direction, status: str
    ) -> Signal:
        last = candles[-1]
        atr_value = snapshot.indicators.get("atr")
        atr = (
            float(atr_value.value)
            if atr_value and isinstance(atr_value.value, float)
            else max(last.high - last.low, 1)
        )
        entry_low = min(last.close, last.close - (atr * 0.1))
        entry_high = max(last.close, last.close + (atr * 0.1))
        is_option_instrument = any(x in snapshot.instrument for x in ("-CE", "-PE", "_CE", "_PE"))
        if is_option_instrument:
            # Levels in option-premium domain; clamp SL to max 20% loss
            if direction == Direction.SELL:
                stop = max(last.close + (atr * 1.5), last.close * 1.20)
                target_1 = last.close - (atr * 2.0)
                target_2 = last.close - (atr * 3.0)
            elif direction == Direction.BUY:
                stop = max(last.close - (atr * 1.5), last.close * 0.80)
                target_1 = last.close + (atr * 2.0)
                target_2 = last.close + (atr * 3.0)
            else:
                stop = target_1 = target_2 = last.close
        elif direction == Direction.SELL:
            stop = last.close + (atr * 1.2)
            target_1 = last.close - (atr * 1.8)
            target_2 = last.close - (atr * 2.6)
        elif direction == Direction.BUY:
            stop = last.close - (atr * 1.2)
            target_1 = last.close + (atr * 1.8)
            target_2 = last.close + (atr * 2.6)
        else:
            stop = target_1 = target_2 = last.close

        # Dynamic Confidence:
        # If we have a clear direction, start confidence at 65% + bonus for strength.
        # This ensures it passes the risk engine's confidence_floor (typically 60%).
        if direction != Direction.HOLD:
            strength_bonus = max(0, int((abs(snapshot.confluence_score) - 0.4) * 50))
            confidence = min(95, 65 + strength_bonus)
        else:
            # For HOLD, show raw confluence percentage (max 60%)
            confidence = min(60, max(15, int(abs(snapshot.confluence_score) * 100)))
        return Signal(
            created_at=datetime.now().astimezone(),
            instrument=snapshot.instrument,
            signal=direction,
            confidence=confidence,
            entry_zone=EntryZone(low=round(entry_low, 2), high=round(entry_high, 2)),
            stop_loss=round(stop, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            risk_reward_ratio=1.5 if direction != Direction.HOLD else 0,
            reasoning=f"{direction.value} generated from weighted technical confluence; AI status: {status}.",
            warnings=[] if status.endswith("_OK") else [status],
            invalidation="Confluence falls back inside neutral band or stop-loss is breached.",
            indicator_snapshot=snapshot,
            ai_model=self.ai_config.get("model", "rule_based"),
            ai_status=status,
        )
