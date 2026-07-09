"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { TrendingUp, AlertTriangle, History, PauseCircle, RefreshCcw, RotateCcw, ShieldCheck, Wifi, Activity } from "lucide-react";
import { EquityChart } from "../components/EquityChart";
import { DailyHistory } from "../components/DailyHistory";
import { IndicatorPanel } from "../components/IndicatorPanel";
import { PositionsPanel } from "../components/PositionsPanel";
import { PriceChart } from "../components/PriceChart";
import { SignalFeed } from "../components/SignalFeed";
import { TradeLog } from "../components/TradeLog";
import type { DashboardSnapshot } from "../lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:7070";
const WS_URL = API_URL.replace("http", "ws");

const formatSymbol = (sym: string) => {
  if (sym.includes(":")) {
    const [strat, inst] = sym.split(":");
    return {
      strategy: strat.replace(/_/g, " "),
      instrument: inst.replace(/_/g, " ")
    };
  }
  if (sym.endsWith("_FUT") || sym.endsWith("_FUT_FUT") || sym.includes("FUT")) {
    return {
      strategy: "FUTURES",
      instrument: sym.replace(/_FUT(_FUT)?$/, "").replace(/_/g, " ")
    };
  }
  return {
    strategy: "OPTIONS",
    instrument: sym.replace(/_/g, " ")
  };
};

export default function DashboardPage() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [wsLive, setWsLive] = useState(false);
  const [prevPrices, setPrevPrices] = useState<Record<string, number>>({});
  const [flashStates, setFlashStates] = useState<Record<string, "up" | "down" | null>>({});

  const fetchSnapshot = useCallback(async () => {
    const response = await fetch(`${API_URL}/api/snapshot`, { cache: "no-store" });
    setSnapshot(await response.json());
  }, []);

  useEffect(() => {
    fetchSnapshot();
    const ws = new WebSocket(`${WS_URL}/ws`);
    ws.onopen = () => setWsLive(true);
    ws.onclose = () => setWsLive(false);
    ws.onmessage = (event) => setSnapshot(JSON.parse(event.data));
    return () => ws.close();
  }, [fetchSnapshot]);

  useEffect(() => {
    if (!snapshot) return;
    const newFlashes: Record<string, "up" | "down" | null> = {};
    const currentPrices: Record<string, number> = {};

    ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "INDIAVIX"].forEach((symbol) => {
      const history = snapshot.market_data_history?.[symbol] || [];
      const latestPrice = (snapshot.latest_indicators?.[symbol] as any)?.price ?? (history.length > 0 ? history[history.length - 1].price : null);

      if (latestPrice !== null && latestPrice !== undefined) {
        const priceNum = typeof latestPrice === "number" ? latestPrice : parseFloat(latestPrice as any);
        currentPrices[symbol] = priceNum;

        const prev = prevPrices[symbol];
        if (prev !== undefined && prev !== priceNum) {
          newFlashes[symbol] = priceNum > prev ? "up" : "down";
        }
      }
    });

    setPrevPrices(currentPrices);

    if (Object.keys(newFlashes).length > 0) {
      setFlashStates((prev) => ({ ...prev, ...newFlashes }));
      const timer = setTimeout(() => {
        setFlashStates((prev) => {
          const cleared = { ...prev };
          Object.keys(newFlashes).forEach((k) => {
            cleared[k] = null;
          });
          return cleared;
        });
      }, 1000);
      return () => clearTimeout(timer);
    }
  }, [snapshot]);

  const killSwitch = async () => {
    const response = await fetch(`${API_URL}/api/kill-switch`, { method: "POST" });
    setSnapshot(await response.json());
  };

  const reset = async () => {
    const response = await fetch(`${API_URL}/api/reset`, { method: "POST" });
    setSnapshot(await response.json());
  };

  const backtestToday = async () => {
    try {
      const response = await fetch(`${API_URL}/api/backtest`, { method: "POST" });
      const payload = await response.json();
      if (payload.snapshot) {
        setSnapshot(payload.snapshot);
      }
    } catch (error) {
      console.error("Backtest failed:", error);
    }
  };

  const [isSeeding, setIsSeeding] = useState(false);

  const goLive = async () => {
    setIsSeeding(true);
    try {
      const response = await fetch(`${API_URL}/api/seed`, { method: "POST" });
      setSnapshot(await response.json());
    } catch (error) {
      console.error("Go Live failed:", error);
    } finally {
      setIsSeeding(false);
    }
  };

  const renderTickerItems = () => {
    const items = [
      { key: "NIFTY", label: "NIFTY 50" },
      { key: "BANKNIFTY", label: "BANK NIFTY" },
      { key: "RELIANCE", label: "RELIANCE" },
      { key: "HDFCBANK", label: "HDFC BANK" },
      { key: "INDIAVIX", label: "INDIA VIX" }
    ];

    return items.map(({ key, label }) => {
      const history = snapshot?.market_data_history?.[key] || [];
      const latestInd = snapshot?.latest_indicators?.[key] as any;
      const currentPrice = latestInd?.price ?? (history.length > 0 ? history[history.length - 1].price : null);
      const openPrice = history.length > 0 ? history[0].price : null;

      if (currentPrice === null || currentPrice === undefined) return null;

      const priceNum = typeof currentPrice === "number" ? currentPrice : parseFloat(currentPrice as any);
      const openNum = typeof openPrice === "number" ? openPrice : (openPrice ? parseFloat(openPrice as any) : priceNum);

      const change = priceNum - openNum;
      const changePct = openNum !== 0 ? (change / openNum) * 100 : 0;
      const isUp = change >= 0;

      const flash = flashStates[key];
      const flashClass = flash === "up" ? "ticker-flash-up" : flash === "down" ? "ticker-flash-down" : "";

      return (
        <div key={key} className={`ticker-item ${flashClass}`}>
          <span className="ticker-name">{label}</span>
          <span className="ticker-price" style={{ color: "var(--text)" }}>
            {priceNum.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
          <span className="ticker-change" style={{ color: isUp ? "var(--green)" : "var(--red)" }}>
            {isUp ? "▲" : "▼"}
            {Math.abs(change).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ({isUp ? "+" : ""}{changePct.toFixed(2)}%)
          </span>
        </div>
      );
    });
  };

  if (!snapshot) {
    return <main className="shell"><div className="loading">Connecting to ChartEdge AI...</div></main>;
  }

  const metrics = snapshot.metrics || {};

  return (
    <>
      <div className="ticker-wrap">
        <div className="ticker-status">
          <div className="ticker-status-dot" style={{ background: wsLive ? "var(--green)" : "var(--red)", boxShadow: wsLive ? "0 0 8px var(--green)" : "0 0 8px var(--red)" }}></div>
          <span>{wsLive ? "Live Feed" : "Disconnected"}</span>
        </div>
        <div className="ticker-track-container">
          <div className="ticker-track">
            {renderTickerItems()}
            {renderTickerItems()}
          </div>
        </div>
      </div>

      <main className="shell">
        <header className="topbar">
          <div>
          <p className="eyebrow">NSE Technical Analysis SaaS</p>
          <h1>ChartEdge AI</h1>
        </div>
        <div className="actions">
          <Link href="/options">
            <button style={{ background: "rgba(77, 148, 191, 0.15)", borderColor: "var(--accent)", color: "var(--text)" }}>
              <TrendingUp size={18} /> Strategies
            </button>
          </Link>
          <Link href="/positional-stocks">
            <button style={{ background: "rgba(77, 148, 191, 0.15)", borderColor: "var(--accent)", color: "var(--text)" }}>
              <TrendingUp size={18} /> Positional Stocks
            </button>
          </Link>
          <span className={`feed ${wsLive ? "on" : "off"}`}>
            <Wifi size={16} /> {snapshot.feed_health}
            <small style={{ marginLeft: '4px', opacity: 0.7 }}>
              {new Date(snapshot.market_time).toLocaleTimeString("en-IN", { timeZone: 'Asia/Kolkata', hour: "2-digit", minute: "2-digit", hour12: false })}
            </small>
          </span>
          <button onClick={goLive} disabled={isSeeding} title="Fetch historical data and start live monitoring" style={{ background: isSeeding ? "rgba(255,255,255,0.05)" : "rgba(77, 191, 140, 0.15)", borderColor: isSeeding ? "rgba(255,255,255,0.1)" : "var(--green)", color: "var(--text)" }}>
            <Activity size={18} className={isSeeding ? "spin" : ""} /> {isSeeding ? "Going Live..." : "Go Live"}
          </button>
          <button onClick={backtestToday} title="Replay today's 09:30-15:00 historical session"><History size={18} /> Backtest</button>
          <button onClick={reset} title="Clear paper state and release kill switch"><RotateCcw size={18} /></button>
          <button onClick={fetchSnapshot} title="Refresh snapshot"><RefreshCcw size={18} /></button>
          <button className="danger" onClick={killSwitch} title="Halt signals and close paper positions">
            <PauseCircle size={18} /> Kill
          </button>
        </div>
      </header>

      <section className="metrics">
        <Metric 
          label="Realized P&L" 
          value={`${metrics.realized_pnl?.toFixed(2) ?? "0.00"} (${metrics.realized_pnl_pct?.toFixed(2) ?? "0.00"}%)`} 
          tone={(metrics.realized_pnl ?? 0) >= 0 ? "gain" : "loss"} 
        />
        <Metric 
          label="Open P&L" 
          value={`${metrics.open_pnl?.toFixed(2) ?? "0.00"} (${metrics.open_pnl_pct?.toFixed(2) ?? "0.00"}%)`} 
          tone={(metrics.open_pnl ?? 0) >= 0 ? "gain" : "loss"} 
        />
        <Metric label="Win Rate" value={`${metrics.win_rate?.toFixed(1) ?? "0.0"}%`} />
        <Metric label="Money In" value={`₹${metrics.total_invested?.toLocaleString() ?? "0"}`} />
        <Metric 
          label="Money Out" 
          value={`₹${metrics.total_recovered?.toLocaleString() ?? "0"}`} 
          tone={(metrics.total_recovered ?? 0) >= (metrics.total_invested ?? 0) ? "gain" : "loss"} 
        />
        <Metric label="Profit Factor" value={metrics.profit_factor?.toFixed(2) ?? "0.00"} />
      </section>

      {snapshot.kill_switch_enabled ? (
        <div className="alert"><AlertTriangle size={18} /> Kill switch is enabled. Signal execution is paused.</div>
      ) : (
        <div className="notice"><ShieldCheck size={18} /> Paper engine active with confidence floor and one-position-per-instrument controls.</div>
      )}

      <section className="grid">
        <section className="panel span2">
          <div className="panelHeader">
            <h2>Paper Equity Curve</h2>
            <span>{new Date(snapshot.market_time).toLocaleString("en-IN", { timeZone: 'Asia/Kolkata', hour12: false })}</span>
          </div>
          <EquityChart data={snapshot.equity_curve || []} />
        </section>

        <section className="panel">
          <div className="panelHeader">
            <h2>Market Context</h2>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {/* Institutional Context */}
            {(() => {
              const latestInds = Object.values(snapshot.latest_indicators || {});
              const context = latestInds[0]?.market_context;
              if (!context) return <div className="loading" style={{ padding: '1rem', opacity: 0.5 }}>Waiting for market context...</div>;
              
              return (
                <div className="contextStrip" style={{ 
                  display: "grid", 
                  gridTemplateColumns: "1fr 1fr 1fr", 
                  gap: "0.5rem", 
                  padding: "0.75rem", 
                  background: "rgba(255,255,255,0.03)", 
                  borderRadius: "6px",
                  border: "1px solid rgba(255,255,255,0.05)"
                }}>
                  <ContextMetric 
                    label="RELIANCE" 
                    value={context.reliance_trend} 
                    tone={context.reliance_trend === "BULLISH" ? "gain" : context.reliance_trend === "BEARISH" ? "loss" : ""}
                  />
                  <ContextMetric 
                    label="HDFCBANK" 
                    value={context.hdfc_bank_trend} 
                    tone={context.hdfc_bank_trend === "BULLISH" ? "gain" : context.hdfc_bank_trend === "BEARISH" ? "loss" : ""}
                  />
                  <ContextMetric 
                    label="INDIA VIX" 
                    value={context.india_vix?.toFixed(2) ?? "0.00"} 
                    tone={context.india_vix > 18 ? "loss" : context.india_vix < 12 ? "gain" : ""}
                  />
                </div>
              );
            })()}

            {snapshot.market_data_history?.["NIFTY"] && (
              <PriceChart 
                data={snapshot.market_data_history["NIFTY"]} 
                symbol="NIFTY 50" 
                color="#4dbf8c" 
              />
            )}
            {snapshot.market_data_history?.["BANKNIFTY"] && (
              <PriceChart 
                data={snapshot.market_data_history["BANKNIFTY"]} 
                symbol="BANK NIFTY" 
                color="#4d94bf" 
              />
            )}
          </div>
        </section>

        {/* --- SECTION 1: LIVE OPERATIONS CENTER --- */}
        <div className="section-header">
          <h2>⚡ Live Operations Center</h2>
        </div>
        <SignalFeed signals={snapshot.signals || []} />
        <IndicatorPanel snapshots={snapshot.latest_indicators || {}} />

        {/* --- SECTION 2: PORTFOLIO & POSITIONS --- */}
        <div className="section-header">
          <h2>💼 Active Portfolio</h2>
        </div>
        <PositionsPanel trades={snapshot.open_positions || []} />
        
        <section className="panel">
          <div className="panelHeader">
            <h2>Instrument Breakdown</h2>
          </div>
          <div className="instrumentGrid">
            {Object.entries(metrics.instruments || {}).map(([symbol, stats]: [string, any]) => {
              const info = formatSymbol(symbol);
              return (
                <div key={symbol} className="instrumentCard">
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px", marginBottom: "0.75rem", borderBottom: "1px solid rgba(255,255,255,0.05)", paddingBottom: "0.5rem" }}>
                    <span style={{ fontSize: "0.65rem", opacity: 0.5, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                      {info.strategy}
                    </span>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "8px" }}>
                      <h3 style={{ margin: 0, fontSize: "0.9rem", fontWeight: 600, color: "var(--accent)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={info.instrument}>
                        {info.instrument}
                      </h3>
                      <span style={{ fontSize: "0.75rem", opacity: 0.6, whiteSpace: "nowrap" }}>
                        {stats.trades} Trades
                      </span>
                    </div>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                    <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Win Rate</span>
                    <span style={{ fontSize: "0.8rem", fontWeight: "bold" }}>{stats.win_rate}%</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                    <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Investment</span>
                    <span style={{ fontSize: "0.8rem", fontWeight: "bold", opacity: 0.8 }}>₹{stats.invested?.toLocaleString() ?? "0"}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Net P&L</span>
                    <span style={{ fontSize: "0.8rem", fontWeight: "bold", color: (stats.pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      {(stats.pnl ?? 0) >= 0 ? "+" : ""}₹{(stats.pnl ?? 0).toFixed(2)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* --- SECTION 3: AUDIT & PERFORMANCE --- */}
        <div className="section-header">
          <h2>📜 Performance Audit Trail</h2>
        </div>
        <TradeLog trades={snapshot.closed_trades || []} />
        <DailyHistory />
      </section>
    </main>
    </>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "gain" | "loss" }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function ContextMetric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "2px" }}>
      <span style={{ fontSize: "0.65rem", opacity: 0.5, letterSpacing: "0.5px" }}>{label}</span>
      <strong className={tone} style={{ fontSize: "0.85rem" }}>{value}</strong>
    </div>
  );
}
