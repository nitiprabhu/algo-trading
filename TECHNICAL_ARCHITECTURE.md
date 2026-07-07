# Technical Architecture: ChartEdge Multi-Strategy Trading System

**Last Updated**: Jul 6, 2026 | **System Status**: LIVE

---

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   IndstocksMarketRuntime                    │
│  (Live 1-min candles + Option chain data from INDstocks)    │
└────────────────────┬────────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
    ┌────▼────────┐      ┌───────▼──────┐
    │   INTRADAY  │      │  POSITIONAL  │
    │  (Tier 1)   │      │   (Tier 2)   │
    │ ₹50k capital│      │  ₹100k cap   │
    └─────────────┘      └──────────────┘
         │ (09:15-15:30)      │ (once/week)
         │                    │
    ┌────▼────────┐      ┌────▼──────────┐
    │ PaperTrading│      │PositionalTrading
    │  Engine     │      │ Engine
    └─────────────┘      └────────────────┘
         │                    │
         └────────┬───────────┘
                  │
         ┌────────▼─────────┐
         │  TelegramNotifier │
         │  (Alerts)         │
         └───────────────────┘
```

---

## 1. INTRADAY TRADING (Tier 1)

### Strategy: EagleNiftyT315 Opening Range Breakout

**Capital**: ₹50k (separate pool)  
**Instruments**: NIFTY, BANKNIFTY (1-min options)  
**Hours**: 09:15–15:30 (NSE trading hours)  
**Entry Cutoff**: 10:15 (no new entries after)  
**Position Limit**: 1 active position at a time per instrument  

---

### Entry Trigger Flow

```
09:15 Market Open
  │
  ├─ Build 30-min opening range candle
  │  (09:15–09:45: track HIGH and LOW)
  │
  ├─ Compute technical indicators (every 1-min tick)
  │  ├─ RSI (14), MACD, EMA ribbon
  │  ├─ Volume (vs 10-min avg)
  │  ├─ VWAP, ATR, Supertrend
  │  └─ Bollinger Bands, VIX check (14–22 optimal)
  │
  └─ 09:45 Range Set. Now watch for BREAKOUT
      │
      ├─ [09:46–10:15] Scan for breakout candle
      │  (1-min close breaches 30-min HIGH or LOW)
      │
      └─ When breakout candle found:
          ├─ Check validation:
          │  ├─ 3-min confirmation (next 2 candles stay beyond breakout level?)
          │  ├─ Volume check (breakout vol > 1.5× avg?)
          │  ├─ Body filter (body > 40% of full candle range?)
          │  └─ Time check (before 10:15?)
          │
          ├─ Compute Confluence Score
          │  ├─ Rule-based: RSI, MACD, Volume weights → 0.0–1.0
          │  └─ AI layer: Claude LLM analyzes snapshot
          │      ├─ Bull analyst: "Is this real breakout?"
          │      ├─ Bear analyst: "Is this a fake-out?"
          │      └─ Judge: Final confidence
          │
          ├─ Check Regime
          │  └─ AIRegimeAgent: TRENDING / CHOP / MEAN_REVERTING?
          │
          ├─ Decision Gate
          │  ├─ IF confidence ≥ 0.51 (threshold) AND
          │  ├─ IF ADX ≥ 20 (trending, not choppy) AND
          │  ├─ IF 4h bias matches (uptrend = BUY, downtrend = SELL)
          │  │
          │  └─ ENTRY TRIGGERED ✅
          │
          └─ Execute Trade
             ├─ Entry side: CALL (if breakout up), PUT (if breakout down)
             ├─ Qty: 75 (NIFTY lot size)
             ├─ Strike: ATM or 1 strike OTM (based on probability)
             └─ Log entry: [timestamp, side, strike, entry_price, confidence]
```

---

### Exit Triggers (During 09:15–15:30)

**Three exit paths** (checked every 1-min):

#### Path 1: THETA_MITIGATION (Profit-Take)
```
While position open:
  ├─ Check: Has theta decayed 50% of entry premium?
  ├─ IF YES
  │  └─ Close position (lock profit before vol spike)
  │     └─ Exit reason: THETA_MITIGATION_40M / _50M / _60M
  │     └─ Typical PnL: +₹400–₹800 per trade
```

#### Path 2: SL (Stop-Loss)
```
While position open:
  ├─ Check: Position loss ≥ 15% of entry premium? 
  ├─ IF YES
  │  └─ Force close (cut losses)
  │     └─ Exit reason: SL
  │     └─ Typical PnL: -₹200–₹400 per trade
```

#### Path 3: EOD_SQUAREOFF (End of Day)
```
At 15:25 (5 min before market close):
  ├─ Check: Any open position?
  ├─ IF YES
  │  └─ Force close (no overnight gap risk)
  │     └─ Exit reason: EOD_SQUAREOFF
  │     └─ Typical PnL: ±₹50–₹150 (whatever current price is)
```

#### Path 4: EXPIRY_HARD_EXIT (On Expiry Day)
```
If expiry day (Tue for weekly NIFTY):
  ├─ At 14:00 (1.5h before close)
  │  └─ Force close all positions
  │     └─ Exit reason: EXPIRY_HARD_EXIT
  │     └─ Typical PnL: +₹2,000–₹5,000 per trade (gamma acceleration)
```

---

## 2. POSITIONAL TRADING (Tier 2)

### Three Strategies (Selectable via `config.yaml`)

**Capital**: ₹100k (separate pool)  
**Check Frequency**: Once per trading day  
**Cycle**: Weekly (enters Wed after Tue expiry, exits at profit/stop/expiry)

---

### Trigger: Entry Day Detection

```
Every day at 09:30:
  │
  ├─ Check: Is today first trading day AFTER last expiry?
  │  ├─ IF last_expiry = None
  │  │  └─ ENTRY DAY = today (cold start)
  │  │
  │  ├─ IF last_expiry = Tue Jun 30
  │  │  └─ ENTRY DAY = Wed Jul 1 (first day after)
  │  │
  │  └─ IF today < last_expiry
  │     └─ NO ENTRY today (wait for next week)
  │
  └─ IF ENTRY DAY:
     │
     ├─ Get current NIFTY spot price
     ├─ Get current VIX
     ├─ Get next expiry date (from chain)
     ├─ Calculate DTE (days to expiry)
     │
     └─ Calculate sigma (volatility distance)
        └─ σ = spot × (VIX / 100) × √(dte / 365)
```

---

### Strategy: CONDOR (Default)

**Entry**: 
```
Iron Condor = Long wings + Short strangle

Strike placement (based on sigma):
  ├─ Short Put: ATM - (0.85 × σ)
  ├─ Long Put:  ATM - (1.30 × σ)
  ├─ Short Call: ATM + (0.85 × σ)
  └─ Long Call: ATM + (1.30 × σ)

Example (spot 19,200, VIX 16, 6 DTE):
  ├─ σ ≈ 290
  ├─ Short strikes: 19,200 ± 247 = [18,953 | 19,447]
  ├─ Long strikes:  19,200 ± 377 = [18,823 | 19,577]
  └─ 4-leg position ready
```

**Entry Premiums** (from option chain):
```
Collect credit:
  ├─ Short Put 18,953:  +₹50
  ├─ Short Call 19,447: +₹48
  ├─ Long Put 18,823:   -₹5 (insurance)
  ├─ Long Call 19,577:  -₹5 (insurance)
  └─ Total credit received: ₹88 × 75 = ₹6,600
```

**Exit Path 1: PROFIT_TAKE (55%)**
```
Daily mark-to-market check:
  ├─ Cost to close all 4 legs (debit)
  ├─ IF debit ≤ 55% of credit (₹88 × 0.55 = ₹48)
  │  └─ CLOSE POSITION
  │     └─ P&L: (88 - 48) × 75 = ₹3,000
  │     └─ Exit reason: PROFIT_TAKE
```

**Exit Path 2: STOP_LOSS (-110%)**
```
Daily mark-to-market check:
  ├─ Cost to close all 4 legs (debit)
  ├─ IF debit ≥ 110% of credit (₹88 × 1.1 = ₹97)
  │  └─ FORCE CLOSE
  │     └─ P&L: (88 - 97) × 75 = -₹675
  │     └─ Exit reason: STOP_LOSS
  │     └─ Note: Caps worst-case loss at -10% per trade
```

**Exit Path 3: EXPIRY**
```
On Tuesday expiry:
  ├─ At 15:30 (close)
  │  └─ Position settled by NSE (all legs expire)
  │     └─ P&L: settlement price - credit received
  │     └─ Exit reason: EXPIRY
```

---

### Strategy: STRADDLE

**Entry**:
```
Short ATM Call + Short ATM Put

Strikes (simple):
  ├─ Short Call: ATM (19,200)
  ├─ Short Put:  ATM (19,200)
  └─ NO wings (undefined risk!)

Premiums:
  ├─ Both legs short ATM
  ├─ Total credit: ~₹300 × 75 = ₹22,500
  └─ Max loss: UNLIMITED (but stopped at debit > 110% credit)
```

**Why higher profit potential?**
- Captures moves in both directions (higher premium)
- Jun 2026: +₹39,244 (best month) vs Condor +₹9,637

**Risk**: May 2026 (chop month) = -₹24,881 single month loss

---

### Strategy: CREDIT_SPREAD

**Entry**:
```
Single-side defined-risk spread

IF 5d uptrend:
  ├─ Sell put-spread
  │  ├─ Short Put: 0.10 delta (near ATM)
  │  └─ Long Put:  0.05 delta (deep OTM)
  └─ Max loss: width of spread (defined)

IF 5d downtrend:
  ├─ Sell call-spread
  │  ├─ Short Call: 0.10 delta
  │  └─ Long Call:  0.05 delta
  └─ Max loss: defined
```

**Why lower variance?**
- Single leg = simpler Greeks
- Consistent wins but smaller
- Jun 2026: +₹3,371 (boring but reliable)

---

## 3. Position Management

### Capital Allocation (Live)

```
Total Capital: ₹150k
│
├─ Intraday Pool: ₹50k
│  ├─ Risk per trade: -10% max (₹5k stop-loss)
│  ├─ Typical trade size: ₹400–₹800 per trade
│  ├─ Max concurrent positions: 1 NIFTY + 1 BANKNIFTY
│  └─ Daily limit: Max loss ₹10k/day (kill switch)
│
├─ Positional Pool: ₹100k
│  ├─ Condor size: ₹6,600 credit per trade
│  ├─ Max concurrent: 1 cycle (5 cycles/month)
│  ├─ Capital utilization: ~₹6.6k × 1 = 6.6% per cycle
│  └─ Monthly P&L target: ₹30k–₹50k
│
└─ Monitoring: /api/positional/status (WebSocket + Telegram)
```

### Trade Logging & Persistence

```
Every trade event:
  ├─ Entry logged: trade_id, entry_time, instrument, legs, credit
  ├─ Mark-to-market daily: update debit, check exit triggers
  ├─ Exit logged: exit_time, debit, P&L, reason
  │
  └─ Storage:
     ├─ Intraday: chartedge.db (SQLModel, trades table)
     ├─ Positional: data/positional_trades.json (file-based)
     └─ Both queryable via /api/trades endpoint
```

---

## 4. Data Flow & Signals

### Per 1-Min Tick (Intraday)

```
IndstocksMarketRuntime.on_candle(new_1min_candle)
  │
  ├─ Update candle buffer (NIFTY, BANKNIFTY, INDIAVIX)
  │
  ├─ Compute indicators (vectorized)
  │  └─ RSI, MACD, EMA, VWAP, Supertrend, Bollinger, ATR
  │
  ├─ Check ORB breakout (EagleNiftyT315.get_signal)
  │  ├─ Is range set? (09:15–09:45)
  │  ├─ Is this a breakout candle?
  │  ├─ Is validation passed (3-candle, volume, body)?
  │  └─ Return signal or None
  │
  ├─ Compute confluence score
  │  ├─ Rule-based weights (technical)
  │  └─ AI score (Claude LLM)
  │
  ├─ PaperTradingEngine.maybe_enter()
  │  ├─ If signal ≥ threshold: ENTRY
  │  └─ Log trade
  │
  ├─ PaperTradingEngine.mark_to_market()
  │  ├─ Check all open positions for exit triggers
  │  ├─ Theta-decay, SL, EOD, expiry
  │  └─ Exit if criteria met
  │
  ├─ Update metrics (win%, PnL, Sharpe)
  │
  └─ Telegram alert (if entry/exit)
```

### Per Trading Day (Positional)

```
PositionalRuntime.check_once_per_day()
  │
  ├─ Get NIFTY spot, VIX
  ├─ Get option chain for next expiry
  │
  ├─ PositionalTradingEngine.maybe_enter()
  │  ├─ Is today an entry day?
  │  ├─ Size legs based on strategy (Condor / Straddle / Spread)
  │  ├─ Fetch premiums from chain
  │  ├─ Calculate net credit
  │  ├─ IF credit > 0: ENTRY
  │  └─ Log trade (entry_date, expiry, legs, credit)
  │
  ├─ PositionalTradingEngine.mark_to_market()
  │  ├─ Get current option prices (from chain)
  │  ├─ Calculate debit to close
  │  ├─ Check exit triggers:
  │  │  ├─ Profit-take (55% collected?)
  │  │  ├─ Stop-loss (debit > 110%?)
  │  │  ├─ Expiry reached?
  │  │  └─ EXIT if any trigger hit
  │  │
  │  └─ Update trade: exit_time, debit, P&L, reason
  │
  ├─ Save state to JSON
  │
  └─ Telegram alert (entry/exit + legs + P&L)
```

---

## 5. Conflict Management (Intraday + Positional)

### Same Instrument, Both Strategies Active

```
Example: NIFTY
│
├─ Intraday position open: Long 75 NIFTY Call 19,450
│  └─ Entered 10:15, expected exit 13:00 (theta profit-take)
│
├─ Positional cycle ongoing: Iron Condor
│  ├─ Long Call  19,577 (wing)
│  ├─ Short Call 19,447 (short leg)
│  └─ [other legs...]
│
└─ NO CONFLICT because:
   ├─ Intraday is MICRO (1 option leg, short-term)
   ├─ Positional is MACRO (4 legs, week-long)
   ├─ Intraday closes before day-end (no overnight)
   ├─ Positional Greeks hedge each other (condor neutral-delta)
   └─ Capital pools separate (₹50k vs ₹100k)
```

---

## 6. Entry/Exit Decision Tree

### Intraday (EagleNiftyT315)

```
Entry Gate:
  ├─ Range set? (09:45) ✓
  ├─ Breakout detected? ✓
  ├─ Validation passed? (volume, body, 3-candle) ✓
  ├─ Confidence ≥ 0.51? ✓
  ├─ ADX ≥ 20 (trending)? ✓
  ├─ 4h bias matches? ✓
  ├─ Before 10:15? ✓
  │
  └─ ALL YES → ENTRY
      │
      └─ While open:
         ├─ THETA profit-take ✓
         ├─ SL ✓
         ├─ EOD squareoff ✓
         └─ Expiry hard-exit ✓
```

### Positional (Condor/Straddle/Spread)

```
Entry Gate:
  ├─ Today first trading day after expiry? ✓
  ├─ Expiry date found in chain? ✓
  ├─ Credit > 0? ✓
  │
  └─ ALL YES → ENTRY
      │
      └─ While open:
         ├─ 55% profit-take ✓
         ├─ 110% stop-loss ✓
         └─ Expiry settlement ✓
```

---

## 7. Real-Time Monitoring

### APIs

```
GET /api/trades
  ├─ Return: All closed trades for day
  ├─ Filters: instrument, status, time_range
  └─ Use: Daily P&L tracking

GET /api/positional/status
  ├─ Return: Current positional trade (if open)
  ├─ Fields: entry_date, legs, credit, current_debit, days_to_expiry
  └─ Update: Every 5 min

WebSocket /ws
  ├─ Stream: 1-min candle + trade alerts
  ├─ Payload: {instrument, timestamp, close, signal, trade_event}
  └─ Consumer: Frontend dashboard + Telegram bot
```

### Alerts (Telegram)

```
ON INTRADAY ENTRY:
  "🎯 NIFTY 19,450 CE | Entry 10:15 | Conf 0.68 | +₹400 target"

ON POSITIONAL ENTRY:
  "📊 Condor 19,200 | Credit ₹88 × 75 | Jul 1-8 | Win% 75%"

ON EXIT (Win):
  "✅ +₹756 | THETA decay | 2026-06-02 10:30"

ON EXIT (Loss):
  "⚠️ -₹382 | MAX_LOSS_GUARD | Wrong direction | 2026-06-29"
```

---

## 8. Risk Controls (Circuit Breakers)

### Hard Stops

```
Intraday:
  ├─ Max loss/trade: -10% per position
  ├─ Max loss/day: -₹10k (kill switch)
  ├─ Max concurrent: 1 per instrument
  └─ Entry cutoff: 10:15

Positional:
  ├─ Max loss/trade: -10% (1.1× credit stop)
  ├─ Profit-take: 55% (protect gains)
  └─ Expiry: Auto-close at market close

Both:
  ├─ No weekend positions (Friday EOD squareoff)
  ├─ No over-leverage (capital check before entry)
  └─ Regime check (skip if AIRegimeAgent = CHOP)
```

---

## 9. Example: Full Day Flow (Wed Jul 10, 2026)

```
09:15 Market Open
  ├─ Positional runtime checks: Is Wed first day after Tue Jul 8 expiry? YES
  ├─ Condor enters: credit ₹88 × 75 = ₹6,600
  └─ Alert: "📊 Condor entered, 75% win target"

09:45 Range Set (Intraday)
  └─ ORB strategy now watching for breakout

10:10 BREAKOUT SIGNAL (Intraday)
  ├─ NIFTY closes above 30-min high
  ├─ Confidence 0.68 (RSI + AI)
  ├─ Entry 75 NIFTY 19,450 CE @ ₹42
  └─ Alert: "🎯 NIFTY breakout +75% target, SL -15%"

10:30 Position +₹250
  └─ Monitoring for theta-decay profit-take

11:00 Condor mark-to-market
  ├─ Debit to close: ₹65 (25% profit already)
  ├─ Check: Is this 55% of credit (₹48)? No.
  └─ Hold

13:00 Intraday Theta-Decay Triggered
  ├─ Close NIFTY 19,450 CE @ ₹22
  ├─ P&L: (42 - 22) × 75 = +₹1,500
  └─ Alert: "✅ +₹1,500 | Theta decay | Holding >15%"

14:00 Condor still at ₹65 debit
  ├─ Profit: (88 - 65) × 75 = ₹1,725
  └─ Hold

15:25 EOD Check
  ├─ Intraday: No open position (already closed 13:00)
  ├─ Positional: Condor still open (holds until 55% or Tue expiry)
  └─ EOD squareoff: N/A

16:00 End of Day
  ├─ Intraday P&L: +₹1,500 (1 trade)
  ├─ Positional P&L: +₹1,725 (open, unrealized)
  └─ Daily P&L: +₹3,225 on ₹150k = 2.15%
```

---

## Architecture Strengths

✅ **Isolation**: Separate capital pools → separate risk profiles  
✅ **Non-correlation**: Intraday (short-term scalp) vs Positional (week-long premium harvest)  
✅ **Asymmetric payoff**: Small losses, large wins (4.37× profit factor)  
✅ **Automated checks**: All entry/exit logic codified (no discretion)  
✅ **Real-time monitoring**: Telegram alerts + API endpoints  
✅ **Scalability**: Add more instruments/strategies without touching core engine

---

## Files Implementing This Architecture

| File | Role |
|------|------|
| `api.py` | FastAPI server, WebSocket stream, /trades endpoint |
| `indstocks.py` | Market runtime, candle feed, option chain lookup |
| `indicators.py` | RSI, MACD, EMA, VWAP, Supertrend, ATR, Bollinger |
| `confluence.py` | Weighted technical score (RSI + Vol + Supertrend) |
| `ai_signal.py` | Claude LLM call, debate mode, signal JSON |
| `strategies.py` | EagleNiftyT315 ORB logic |
| `paper_trading.py` | Intraday execution, SL/T1/T2, trade logging |
| `positional_trading.py` | Condor/Straddle/Spread entry/exit |
| `positional_runtime.py` | Daily check, mark-to-market, Telegram alerts |
| `derivative_manager.py` | Option token lookup (NIFTY/BANKNIFTY nearest expiry) |
| `regime_agent.py` | Classify market (TRENDING / CHOP / MEAN_REVERT) |
| `telegram.py` | Bot alerts (entry, exit, P&L) |
| `database.py` | SQLite trade persistence |

---

End architecture flow.
