# ChartEdge AI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

NSE intraday + positional algo-trading platform for NIFTY/BANKNIFTY. Single-process FastAPI backend runs a live indicator → confluence → AI signal pipeline with paper-trade execution, plus fully isolated positional modules (weekly options, large-cap equity swing). Next.js dashboard for the live feed.

**Status:** paper trading only. No live order placement.

## What's built

- FastAPI backend (`services/chartedge_core/api.py`) running one background market-data loop (mock simulator or live INDstocks feed).
- Multi-indicator engine: RSI, MACD, EMA ribbon, VWAP, Supertrend, volume, ATR, Bollinger.
- Weighted confluence scoring → BUY/SELL/HOLD, per-instrument thresholds in `shared/config.yaml`.
- AI signal layer: Anthropic or OpenAI provider, optional multi-agent debate (bull/bear/judge), rule-based fallback when no API key is set.
- Intraday paper trading: confidence floor, one open position per instrument, SL/T1/T2, breakeven trail, kill switch.
- **Weekly options positional module** (`positional_trading.py`) — condor/straddle/credit-spread on nearest NIFTY weekly expiry. Own capital pool, own DB table, opt-in via config.
- **Positional stocks module** (`positional_stocks.py`) — long-only large-cap equity swing trades (daily technical confluence). Own capital pool, own DB table, opt-in.
- **Dynamic AI regime agent** — classifies market regime pre-session and adjusts the day's confluence threshold.
- Postgres persistence (`DATABASE_URL`) with SQLite fallback for local dev; config can be overridden at runtime from the DB.
- Telegram trade alerts (optional).
- Next.js dashboard: live signal feed, open positions, equity curve, trade log, indicator confluence.

See [CLAUDE.md](CLAUDE.md) for the full architecture breakdown and file-by-file module reference.

## Run locally

Backend:

```bash
pip install -e ".[dev]"
uvicorn services.chartedge_core.api:app --reload --port 7000
```

Frontend:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:7000 npm run dev
```

Open `http://localhost:3000`.

### Docker Compose

```bash
docker compose up
```

Spins up API on `localhost:7070` and frontend on `localhost:9000`.

## Environment

```bash
cp .env.example .env
```

| Var | Purpose |
|-----|---------|
| `CHARTEDGE_DATA_SOURCE` | `mock` (default, local demo) or `indstocks` (live market data) |
| `INDMONEY_TOKEN` | INDstocks API token, required for `indstocks` data source |
| `ANTHROPIC_API_KEY` | Enables Claude reasoning; absent → deterministic rule-based fallback (`AI_UNAVAILABLE`) |
| `OPENAI_API_KEY` | Alternative AI provider, selected via `ai.provider` in `shared/config.yaml` |
| `DATABASE_URL` | PostgreSQL connection string; absent → local SQLite (`chartedge.db`) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Optional trade-entry/exit alerts |

## Tests & backtests

```bash
pytest                          # test suite
python run_today_backtest.py    # replay today's data through the full pipeline
python run_weekly_backtest.py
python run_monthly_backtest.py
python services/chartedge_core/backtest_runner.py   # full offline backtest runner
```

Root-level `run_*.py` scripts are standalone backtest runners; see [CLAUDE.md](CLAUDE.md) for the complete list and backtesting conventions.

## Backtest results

All numbers below are from real NSE F&O bhavcopy settlement data (`data/nse_bhavcopy/`), not synthetic/Black-Scholes pricing. Full per-cycle detail and methodology in each linked report.

**Headline: NIFTY weekly condor (the live default) is the strongest, most consistent result** — 75% win / +₹71,677 over 2 years (106 cycles), and 76% win / +₹41,265 over the most recent 1 year (54 cycles) in a separate report. Same strategy, same live default config, positive across both windows. BANKNIFTY weekly (same condor logic, different index) is *not* validated the same way — net -₹4,646 in the 1yr window despite a 69% win rate, so treat it as unproven rather than "the same edge on another index."

| Module | Window | Result | Report |
|---|---|---|---|
| Weekly NIFTY Condor (`positional_trading.py`, live default) | Jul 2024–Jul 2026, 106 cycles | 75% win, +₹71,677 | [real_data_condor_backtest_2024-07_to_2026-07.md](reports/real_data_condor_backtest_2024-07_to_2026-07.md) |
| Weekly NIFTY Straddle (`positional_trading.py`, opt-in) | Jul 2024–Jul 2026, 106 cycles | 64% win, +₹159,964 (undefined-risk, largest single-month loss -₹24,881) | [real_data_options_strategy_comparison_2024-07_to_2026-07.md](reports/real_data_options_strategy_comparison_2024-07_to_2026-07.md) |
| Weekly NIFTY Credit Spread (`positional_trading.py`, opt-in) | Jul 2024–Jul 2026, 106 cycles | 83% win, +₹40,886, flattest equity curve | same report as above |
| BANKNIFTY Monthly Condor | Jul 2024–Jul 2026, 21 cycles | 71% win, +₹10,806 | same report as above |
| Positional Stocks swing (`positional_stocks.py`, live default) | 12mo, 20-stock universe, 50 trades | 60% win, +9.78% return, 2.10x profit factor | numbers from `shared/config.yaml` comment above `positional_stocks_risk:` — no standalone report file; reproduce with `python run_positional_stocks_backtest.py` |
| Intraday futures swing (`futures_trader.py`) | Jul 2024–Jul 2026, 6 trades | 50% win, **net -₹22,721** (small sample) | [real_data_swing_futures_backtest_2024-07_to_2026-07.md](reports/real_data_swing_futures_backtest_2024-07_to_2026-07.md) |
| Multi-symbol condor — **weekly expiry** (NIFTY, BANKNIFTY) | Jul 2025–Jul 2026 | NIFTY: 54 cycles, 76% win, +₹41,265. BANKNIFTY: 13 cycles, 69% win, **net -₹4,646** | [real_data_multi_symbol_condor_2025-07_to_2026-07.md](reports/real_data_multi_symbol_condor_2025-07_to_2026-07.md) |
| Multi-symbol condor — **monthly expiry** (10 large-cap stocks, no weeklies exist for stock options in India) | Jul 2025–Jul 2026 | Best: HDFCBANK 85% win +₹23,430, INFY 69% win +₹30,220. Worst: SBIN 46% win **-₹74,663**, AXISBANK 46% win **-₹43,875** | same report as above |

**Read before trusting these:** exits are checked once per trading day off EOD settlement prices (bhavcopy has no intraday ticks) — a daily-bar proxy for rules designed to run intraday, not an exact replay. Real VIX only available from mid-2025 onward; earlier months in the 2yr window fall back to a fixed VIX=15 estimate for strike sizing. See [reports/options_profitability_investigation_2026-06-02.md](reports/options_profitability_investigation_2026-06-02.md) for the deeper investigation into synthetic-vs-real data and regime-gating overfit risk that shaped these defaults.

## Deployment

Deploys to Render via [render.yaml](render.yaml) (Postgres + web service). `DATABASE_URL` and `INDMONEY_TOKEN` are configured through the Render dashboard; see the "Key env vars" table in [CLAUDE.md](CLAUDE.md).

## Positional modules

Both run independently of the intraday engine — separate capital pool, separate DB table each. Toggle via `shared/config.yaml`:

- `positional_risk.enabled` — weekly NIFTY options (condor/straddle/credit_spread), currently **on**.
- `positional_stocks_risk.enabled` — large-cap equity swing (yfinance-based, no API token needed), currently **on**.

## License

MIT — see [LICENSE](LICENSE).
