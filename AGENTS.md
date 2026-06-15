# AGENTS.md

> [!IMPORTANT]
> **Before proposing or implementing any strategy, risk rule, or parameter changes, you MUST consult [BENCHMARK.md](file:///Users/nithish-prabhu/Downloads/intra-day/BENCHMARK.md).** Any new algorithm must beat the baseline benchmark of **₹+69,929.05** combined PnL across April-June 2026 without violating the ₹2L capital limits and the 2.5% daily drawdown rule.

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Commands

**Backend**
```bash
pip install -e ".[dev]"
uvicorn services.chartedge_core.api:app --reload --port 7000
```

**Frontend**
```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:7070 npm run dev
```

**Tests**
```bash
pytest                          # all tests
pytest tests/test_ai_signal.py  # single file
```

**Lint**
```bash
ruff check .
ruff format .
```

**Backtest**
```bash
python run_today_backtest.py           # today's data backtest
python run_weekly_backtest.py          # current week
python run_monthly_backtest.py         # full month
python run_regime_agent_backtest.py    # regime-aware threshold test
python run_trio_comparison.py          # compare AI debate / no-AI / options strategies
python services/chartedge_core/backtest_runner.py  # full backtest runner
```

**Docker**
```bash
docker compose up                      # spins api (8000) + frontend (3000)
```

## Architecture

Single-process FastAPI backend (`services/chartedge_core/api.py`) runs everything in-process. On startup it spawns one `runtime.run()` background task that is either `MarketSimulator` (mock) or `IndstocksMarketRuntime` (live), selected by `CHARTEDGE_DATA_SOURCE` env var.

**Signal pipeline (per tick):**
1. `indstocks.py` / `simulation.py` — feeds raw candles into runtime
2. `indicators.py` — computes RSI, MACD, EMA ribbon, VWAP, Supertrend, volume, ATR, Bollinger per candle
3. `confluence.py` — weighted score → BUY/SELL/HOLD direction
4. `ai_signal.py` — sends snapshot to AI provider (Anthropic or OpenAI) via `prompt_builder.py`; falls back to `rule_based` when API key absent
5. `paper_trading.py` — enforces confidence floor, one-position-per-instrument, SL/T1/T2, T1 breakeven trail, kill switch
6. `database.py` — persists signals and trades via SQLModel to `chartedge.db` (SQLite) or PostgreSQL when `DATABASE_URL` is set

**Config:** `shared/config.yaml` is the master config; loaded by `config.py`. `DATABASE_URL` enables DB-based config overrides at runtime.

**Instruments:** NIFTY and BANKNIFTY are `role: trading`; RELIANCE, HDFCBANK, INDIAVIX are `role: monitor` only.

**Indicator weights** and **confluence thresholds** are per-instrument in `shared/config.yaml` — tune there, not in code.

**AI providers** (`ai_signal.py`): `AnthropicProvider` and `OpenAIProvider` share an `AIProvider` ABC. Provider selected by `ai.provider` in config. Raw JSON returned from LLM is parsed into a `Signal` with `direction`, `confidence`, `entry_zone`, `reasoning`.

**Frontend** (`frontend/`) is Next.js + TypeScript. Connects to backend via REST (`/api/*`) and WebSocket (`/ws`) for live signal feed. `NEXT_PUBLIC_API_URL` must point to the backend.

**`derivative_manager.py`** — resolves nearest-expiry F&O instrument tokens for NIFTY/BANKNIFTY options/futures via INDstocks lookup; called by `indstocks.py` at startup.

**`debate.py`** — multi-agent AI debate layer. Bull analyst, bear analyst, and judge LLM calls run sequentially. Judge emits final `Signal` JSON. Called from `ai_signal.py` when `ai.use_debate: true` in config.

**`regime_agent.py` / `AIRegimeAgent`** — classifies market regime (TRENDING_BULLISH/BEARISH, RANGE_BOUND_CHOP, MEAN_REVERTING) from prior-day candles and sets `confluence_threshold` dynamically before session start. Called by backtest runners and `indstocks.py`.

**`strategies.py`** — rule-based options strategies (`EagleNiftyT315`, `BreakoutScalper`) that run independently of the AI pipeline. `EagleNiftyT315` captures 09:15–09:30 range breakouts; VIX must be 14–18. Used in comparison backtests.

**`telegram.py`** — `TelegramNotifier` sends trade entry/exit alerts. Resolves chat ID automatically via `/getUpdates` at startup if `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars are present.

**`optimization.py`** — offline weight/threshold tuner using backtest P&L as objective; not called from the live runtime.

**`training_logger.py`** — appends signal outcomes to `logs/` for future ML training; called from `paper_trading.py` when a trade closes.

**`backtest_runner.py` / `backtest_direct.py`** — replay historical candles through the full indicator→confluence→AI pipeline offline; results saved to `backtest_analysis*.json`.

**`scratch/`** — throwaway debug scripts, not production code.

## Key env vars

| Var | Purpose |
|-----|---------|
| `CHARTEDGE_DATA_SOURCE` | `mock` (default) or `indstocks` |
| `INDMONEY_TOKEN` | INDstocks API token for live data |
| `ANTHROPIC_API_KEY` | Enables Codex reasoning; absent → `AI_UNAVAILABLE` fallback |
| `OPENAI_API_KEY` | Alternative AI provider |
| `DATABASE_URL` | PostgreSQL URL; absent → SQLite `chartedge.db` |
| `TELEGRAM_BOT_TOKEN` | Bot token for trade alerts |
| `TELEGRAM_CHAT_ID` | Target chat; auto-resolved from `/getUpdates` if absent |

Copy `.env.example` to `.env` for local secrets; `python-dotenv` loads it automatically.

## Backtesting patterns

Root-level `run_*.py` scripts are standalone backtest runners — each passes a date range and config overrides to `backtest_runner.py` or `backtest_direct.py`. Comparison scripts (e.g., `run_trio_comparison.py`) run multiple strategy variants and print a side-by-side summary. Results write to `backtest_analysis*.json` and/or log files in the root.

`is_backtesting=True` on `PaperTradingEngine` skips DB reads/writes and order rate-limiting. `skip_db_load=True` skips recovering open trades from the DB.

## AI pipeline variants

Three modes, selected at runtime/backtest time:

| Mode | Config / flag | Description |
|------|--------------|-------------|
| Rule-based | No API key | `rule_based` fallback; no LLM call |
| Single AI | `ai.use_debate: false` | One LLM call per tick via `AnthropicProvider` or `OpenAIProvider` |
| Debate | `ai.use_debate: true` | Bull → Bear → Judge, three sequential LLM calls; higher latency, higher conviction |

`AIRegimeAgent` runs once pre-session and overrides `confluence_thresholds` in config for that day.

## Imported Claude Cowork project instructions

this is my intraday trading app. it does currently paper trading on nse , takes buy and sell signal and tries to make trade profitable. we have to run this server everyday and montior and makes imprveoements to improde win %

## Historical Backtest Performance (June 2025 - June 2026)

The standard configuration has been backtested over a 13-month period with the following monthly results:

| Month | Combined PnL | Options PnL | Futures PnL | Total Trades | Win Rate | Primary Strategy |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **June 2025** | **₹+25,192.28** | ₹+7,460.25 | ₹+17,732.03 | 34 | 52.9% | `NAKED_BUY` |
| **July 2025** | **₹-17,598.99** | ₹-4,596.45 | ₹-13,002.54 | 80 | 23.8% | `NAKED_BUY` |
| **August 2025** | **₹-8,626.24** | ₹+780.45 | ₹-9,406.69 | 34 | 32.4% | `NAKED_BUY` |
| **September 2025** | **₹+2,541.32** | ₹+1,597.80 | ₹+943.52 | 38 | 47.4% | `NAKED_BUY` |
| **October 2025** | **₹+13,648.77** | ₹+6,397.50 | ₹+7,251.27 | 62 | 45.2% | `NAKED_BUY` |
| **November 2025** | **₹+5,032.50** | ₹+725.55 | ₹+4,306.95 | 52 | 44.2% | `NAKED_BUY` |
| **December 2025** | **₹+13,783.44** | ₹+4,381.50 | ₹+9,401.94 | 65 | 46.2% | `NAKED_BUY` |
| **January 2026** | **₹-21,246.14** | ₹+198.00 | ₹-21,444.14 | 76 | 27.6% | `NAKED_BUY` |
| **February 2026** | **₹+14,807.81** | ₹+4,008.90 | ₹+10,798.91 | 31 | 45.2% | `NAKED_BUY` |
| **March 2026** | **₹+29,503.62** | ₹-1,062.90 | ₹+30,566.52 | 56 | 33.9% | Multi-Strategy |
| **April 2026** | **₹+73,098.37** | ₹+1,868.10 | ₹+71,230.27 | 23 | 82.6% | Multi-Strategy |
| **May 2026** | **₹-18,008.78** | ₹-1,984.05 | ₹-16,024.73 | 33 | 33.3% | Multi-Strategy |
| **June 2026 (1-15)** | **₹+14,839.46** | ₹-1,730.70 | ₹+16,570.16 | 48 | 25.0% | Multi-Strategy |
| **Grand Total** | **₹+126,967.42** | **₹+18,043.95** | **₹+108,923.47** | **632** | **38.5%** | — |

