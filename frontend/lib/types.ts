export type Direction = "BUY" | "SELL" | "HOLD";

export type IndicatorValue = {
  value: number | string | Record<string, number>;
  vote: -1 | 0 | 1;
  state: string;
  weight: number;
};

export type Signal = {
  id: string;
  created_at: string;
  instrument: string;
  signal: Direction;
  confidence: number;
  entry_zone: { low: number; high: number };
  stop_loss: number;
  target_1: number;
  target_2: number;
  risk_reward_ratio: number;
  reasoning: string;
  warnings: string[];
  ai_status: string;
  strategy_name?: string;
  option_type?: "CE" | "PE";
};

export type Trade = {
  id: string;
  instrument: string;
  direction: Direction;
  entry_price: number;
  entry_time: string;
  exit_price: number | null;
  exit_time: string | null;
  exit_reason: string | null;
  pnl: number;
  status: "OPEN" | "CLOSED" | "QUEUED";
  sl_price: number;
  t1_price: number;
  t2_price: number;
  pnl_pct: number;
  quantity: number;
  invested_amount: number;
  option_type?: "CE" | "PE";
};

export type MarketContext = {
  reliance_trend: "BULLISH" | "BEARISH" | "NEUTRAL";
  hdfc_bank_trend: "BULLISH" | "BEARISH" | "NEUTRAL";
  india_vix: number;
  gift_nifty_spread: number;
  basis: number;
};

export type OptionChainRow = {
  strike: number;
  ce_token: string;
  pe_token: string;
};

export type OptionChainData = {
  pcr: number;
  max_pain: number;
  resistance_wall: number;
  support_wall: number;
  oi_change_pct: number;
  chain: OptionChainRow[];
};

export type IndicatorSnapshot = {
  instrument: string;
  timeframe: string;
  candle_time: string;
  indicators: Record<string, IndicatorValue>;
  confluence_score: number;
  higher_timeframe: Record<string, string>;
  market_context?: MarketContext;
  options_data?: OptionChainData;
};

export type DashboardSnapshot = {
  market_time: string;
  feed_health: string;
  signals: Signal[];
  open_positions: Trade[];
  closed_trades: Trade[];
  equity_curve: { time: string; equity: number }[];
  market_data_history: Record<string, { time: string; price: number }[]>;
  latest_indicators: Record<string, IndicatorSnapshot>;
  metrics: {
    total_trades: number;
    win_rate: number;
    profit_factor: number;
    realized_pnl: number;
    realized_pnl_pct: number;
    open_pnl: number;
    open_pnl_pct: number;
    total_invested: number;
    total_recovered: number;
    instruments: Record<string, {
      trades: number;
      win_rate: number;
      pnl: number;
      invested: number;
      open_pnl: number;
    }>;
  };
  kill_switch_enabled: boolean;
};
