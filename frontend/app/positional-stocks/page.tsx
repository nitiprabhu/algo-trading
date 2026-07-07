"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, TrendingUp, Wallet, History } from "lucide-react";

type StockPosition = {
  id: string;
  symbol: string;
  entry_date: string;
  entry_price: number;
  quantity: number;
  status: "OPEN" | "CLOSED";
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl: number;
  pnl_pct: number;
};

type PositionalStocksStatus = {
  enabled: boolean;
  open_positions: Record<string, StockPosition>;
  closed_positions: StockPosition[];
  metrics: {
    open_count?: number;
    closed_count?: number;
    wins?: number;
    win_pct?: number;
    net_pnl?: number;
    capital?: number;
    return_pct?: number;
  };
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:7070";

export default function PositionalStocksPage() {
  const [status, setStatus] = useState<PositionalStocksStatus | null>(null);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_URL}/api/positional_stocks/status`);
        const data = await res.json();
        setStatus(data);
      } catch (e) {
        console.error("Failed to fetch positional stocks status", e);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const openPositions = status?.open_positions ? Object.values(status.open_positions) : [];
  const closedPositions = status?.closed_positions ?? [];
  const metrics = status?.metrics ?? {};

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Long-Only Technical Investment</p>
          <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
            <Link href="/" className="nav-tab" style={{ display: "flex", alignItems: "center", gap: "8px", padding: "4px 8px" }}>
              <ArrowLeft size={16} /> Dashboard
            </Link>
            <h1>Positional Stocks</h1>
          </div>
        </div>
        <div className="actions" style={{ display: "flex", gap: "12px" }}>
          <div className="glass" style={{ padding: "8px 16px", borderRadius: "20px", display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ fontSize: "12px", color: "var(--muted)" }}>STATUS</span>
            <strong style={{ color: status?.enabled ? "var(--green)" : "var(--muted)" }}>
              {status?.enabled ? "LIVE" : "DISABLED"}
            </strong>
          </div>
        </div>
      </header>

      {!status?.enabled && (
        <section className="panel glass" style={{ padding: "1.25rem", marginBottom: "1rem" }}>
          <p style={{ color: "var(--muted)", fontSize: "14px" }}>
            Positional stocks module is disabled. Enable it in shared/config.yaml under
            positional_stocks_risk.enabled to start tracking BUY/SELL signals.
          </p>
        </section>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "14px", marginBottom: "1.5rem" }}>
        <SummaryCard icon={<Wallet size={20} color="var(--accent)" />} label="Capital" value={`₹${(metrics.capital ?? 0).toLocaleString()}`} />
        <SummaryCard icon={<TrendingUp size={20} color="var(--green)" />} label="Net PnL" value={`₹${(metrics.net_pnl ?? 0).toLocaleString()}`} tone={(metrics.net_pnl ?? 0) >= 0 ? "gain" : "loss"} />
        <SummaryCard icon={<History size={20} color="var(--blue)" />} label="Win Rate" value={`${metrics.win_pct ?? 0}%`} />
        <SummaryCard icon={<TrendingUp size={20} color="var(--amber)" />} label="Return" value={`${metrics.return_pct ?? 0}%`} tone={(metrics.return_pct ?? 0) >= 0 ? "gain" : "loss"} />
      </div>

      <section className="panel glass" style={{ padding: "1.5rem", marginBottom: "1.5rem" }}>
        <h2 style={{ marginBottom: "1rem" }}>Open Positions ({openPositions.length})</h2>
        {openPositions.length === 0 ? (
          <p style={{ fontSize: "12px", color: "var(--muted)", textAlign: "center", padding: "20px" }}>
            No open positions -- waiting for a BUY signal.
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--muted)" }}>
                  <th style={{ textAlign: "left", padding: "8px" }}>Symbol</th>
                  <th style={{ textAlign: "left", padding: "8px" }}>Entry Date</th>
                  <th style={{ textAlign: "right", padding: "8px" }}>Entry Price</th>
                  <th style={{ textAlign: "right", padding: "8px" }}>Quantity</th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((p) => (
                  <tr key={p.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.02)" }}>
                    <td style={{ padding: "8px", fontWeight: "bold" }}>{p.symbol}</td>
                    <td style={{ padding: "8px" }}>{p.entry_date}</td>
                    <td style={{ textAlign: "right", padding: "8px" }}>₹{p.entry_price.toLocaleString()}</td>
                    <td style={{ textAlign: "right", padding: "8px" }}>{p.quantity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel glass" style={{ padding: "1.5rem" }}>
        <h2 style={{ marginBottom: "1rem" }}>Closed Positions ({closedPositions.length})</h2>
        {closedPositions.length === 0 ? (
          <p style={{ fontSize: "12px", color: "var(--muted)", textAlign: "center", padding: "20px" }}>
            No closed positions yet.
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--muted)" }}>
                  <th style={{ textAlign: "left", padding: "8px" }}>Symbol</th>
                  <th style={{ textAlign: "left", padding: "8px" }}>Entry</th>
                  <th style={{ textAlign: "left", padding: "8px" }}>Exit</th>
                  <th style={{ textAlign: "left", padding: "8px" }}>Reason</th>
                  <th style={{ textAlign: "right", padding: "8px" }}>PnL</th>
                </tr>
              </thead>
              <tbody>
                {closedPositions.map((p) => (
                  <tr key={p.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.02)" }}>
                    <td style={{ padding: "8px", fontWeight: "bold" }}>{p.symbol}</td>
                    <td style={{ padding: "8px" }}>{p.entry_date} @ ₹{p.entry_price}</td>
                    <td style={{ padding: "8px" }}>{p.exit_date} @ ₹{p.exit_price}</td>
                    <td style={{ padding: "8px" }}>{p.exit_reason}</td>
                    <td style={{ textAlign: "right", padding: "8px" }} className={p.pnl >= 0 ? "gain" : "loss"}>
                      {p.pnl >= 0 ? "+" : ""}₹{p.pnl.toLocaleString()} ({p.pnl_pct.toFixed(2)}%)
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

function SummaryCard({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone?: "gain" | "loss" }) {
  return (
    <div className="panel glass" style={{ padding: "1.25rem", display: "flex", alignItems: "center", gap: "1rem" }}>
      <div style={{ background: "rgba(255,255,255,0.03)", padding: "10px", borderRadius: "8px" }}>{icon}</div>
      <div>
        <span style={{ fontSize: "12px", color: "var(--muted)" }}>{label}</span>
        <strong className={tone} style={{ display: "block", fontSize: "1.25rem" }}>{value}</strong>
      </div>
    </div>
  );
}
