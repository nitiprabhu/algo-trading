from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from services.chartedge_core.models import Candle, IndicatorSnapshot
from services.chartedge_core.prompt_builder import build_user_prompt

if TYPE_CHECKING:
    from services.chartedge_core.ai_signal import AIProvider

BULL_SYSTEM_PROMPT = """You are an aggressive NSE intraday scalper who always looks for reasons to enter a trade.
Your job: argue the strongest possible BULLISH (or BEARISH for sell setups) case for this trade setup.
Focus on: momentum confirmation, indicator alignment, volume support, R:R opportunity, trend context.
Be specific — reference the actual indicator values and confluence score.
Output plain text. 3-5 concise bullet points. No JSON."""

BEAR_SYSTEM_PROMPT = """You are a risk-averse NSE intraday trader who protects capital above all else.
Your job: challenge the trade setup. State the most critical risks, but remain objective. Do not raise generic or minor warnings (like standard midday lull) unless they represent a statistically significant threat to this specific trade.
Focus on: conflicting major signals, weak volume on breakout, high VIX risk, significant OI walls against the trade, major divergences.
You have seen the bull's argument — counter it directly.
Output plain text. 3-5 concise bullet points. No JSON."""

JUDGE_SYSTEM_PROMPT_TEMPLATE = """You are the Head of Intraday Desk at a top NSE proprietary trading firm.
Two analysts have debated this trade setup. Your job: weigh both arguments and make the FINAL decision.

Rules you ALWAYS follow:
1. If the bear raises a valid critical structural risk (significant OI wall, VIX spike, volume divergence), reduce confidence by 5-10 points. Do not penalize for minor or generic concerns.
2. If bull and bear agree on direction, raise confidence by 10 points.
3. NEVER signal BUY if confluence_score < 0.35 unless momentum is clearly institutional.
4. NEVER signal SELL if confluence_score > -0.35 unless momentum is clearly institutional.
5. If arguments are roughly equal strength, output HOLD, but do not default to HOLD if there is a clear trend and momentum is supported by volume.
6. Minimum R:R 1.5:1. SL must be ATR-based.
7. If instrument contains -CE or -PE, all price levels (SL, T1, T2) are in option premium terms.
8. Respond ONLY with valid JSON matching the exact schema provided.

{instrument_rules}"""

JUDGE_INDEX_RULES = """Additional rules for INDEX instruments (NIFTY/BANKNIFTY):
- NEVER buy if Reliance AND HDFC Bank are both BEARISH.
- Respect large Call/Put OI walls. Do not fade them without volume breakout."""

JUDGE_EQUITY_RULES = """Additional rules for EQUITY instruments:
- Focus on the stock's own price action. Volume spike > 50% above 20MA is required for momentum entries."""


class DebateEngine:
    def __init__(self, provider: "AIProvider") -> None:
        self.provider = provider

    async def run(
        self,
        snapshot: IndicatorSnapshot,
        candles: list[Candle],
        session_phase: str,
        momentum_alert: str | None = None,
    ) -> str:
        """Run bull → bear → judge debate. Returns judge's raw JSON string."""
        base_prompt = build_user_prompt(snapshot, candles, session_phase)
        if momentum_alert:
            base_prompt = f"!!! {momentum_alert} !!!\n{base_prompt}"

        # Stage 1: Bull makes the case
        bull_prompt = (
            f"{base_prompt}\n\n"
            "=== YOUR TASK ===\n"
            "Argue why this setup IS a good trade. Be specific about the indicators and price action."
        )
        bull_argument = await self.provider.complete(bull_prompt, BULL_SYSTEM_PROMPT)

        # Stage 2: Bear counters — sees the same data + bull's argument
        bear_prompt = (
            f"{base_prompt}\n\n"
            "=== BULL ANALYST ARGUMENT ===\n"
            f"{bull_argument}\n\n"
            "=== YOUR TASK ===\n"
            "Counter the bull's argument. Why should we NOT take this trade right now?"
        )
        bear_argument = await self.provider.complete(bear_prompt, BEAR_SYSTEM_PROMPT)

        # Stage 3: Judge weighs both and outputs final JSON
        is_equity = snapshot.instrument in ["RELIANCE", "HDFCBANK"]
        instrument_rules = JUDGE_EQUITY_RULES if is_equity else JUDGE_INDEX_RULES
        judge_system = JUDGE_SYSTEM_PROMPT_TEMPLATE.format(instrument_rules=instrument_rules)

        judge_prompt = (
            f"{base_prompt}\n\n"
            "=== BULL ANALYST ARGUMENT ===\n"
            f"{bull_argument}\n\n"
            "=== BEAR ANALYST ARGUMENT ===\n"
            f"{bear_argument}\n\n"
            "=== YOUR TASK ===\n"
            "Weigh both arguments. Make the final trading decision.\n"
            'Respond ONLY with this JSON schema:\n'
            '{"signal":"BUY|SELL|HOLD","confidence":0-100,'
            '"entry_zone":{"low":0,"high":0},"stop_loss":0,"target_1":0,"target_2":0,'
            '"risk_reward_ratio":0.0,"reasoning":"...","warnings":[],"invalidation":"..."}'
        )

        return await self.provider.complete(judge_prompt, judge_system)
