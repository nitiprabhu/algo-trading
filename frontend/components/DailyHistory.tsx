"use client";

import { CalendarDays, History as HistoryIcon, TrendingDown, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type DailyRecord = {
  date: string;
  symbols: Record<string, number>;
  total: number;
};

export function DailyHistory() {
  const [history, setHistory] = useState<DailyRecord[]>([]);

  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const response = await fetch(`${API_URL}/api/history/daily`);
        const data = await response.json();
        setHistory(data.history || []);
      } catch (error) {
        console.error("Failed to fetch history:", error);
      }
    };

    fetchHistory();
    // Refresh history every 30 seconds
    const interval = setInterval(fetchHistory, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="panel span2">
      <div className="panelHeader">
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <CalendarDays size={20} className="accent" />
          <h2>Daily Performance Ledger</h2>
        </div>
      </div>
      <div className="dailyGrid" style={{ display: "grid", gap: "1rem", maxHeight: "400px", overflowY: "auto", minHeight: "100px" }}>
        {history.length === 0 ? (
          <div style={{ 
            display: "flex", 
            flexDirection: "column", 
            alignItems: "center", 
            justifyContent: "center", 
            height: "100%", 
            opacity: 0.5,
            padding: "2rem" 
          }}>
            <HistoryIcon size={32} style={{ marginBottom: "1rem" }} />
            <p>No historical trades found in database yet.</p>
          </div>
        ) : (
          history.map((day) => (
            <div key={day.date} className="dayRow" style={{ 
              padding: "1rem", 
              borderRadius: "12px", 
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.05)",
              borderLeft: `4px solid ${day.total >= 0 ? "var(--gain)" : "var(--loss)"}`
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                <span style={{ fontWeight: "600", fontSize: "1rem", color: "var(--text)" }}>
                  {new Date(day.date).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  {day.total >= 0 ? <TrendingUp size={16} color="var(--gain)" /> : <TrendingDown size={16} color="var(--loss)" />}
                  <strong style={{ color: day.total >= 0 ? "var(--gain)" : "var(--loss)", fontSize: "1.1rem" }}>
                    ₹{day.total.toLocaleString()}
                  </strong>
                </div>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "1.5rem" }}>
                {Object.entries(day.symbols).map(([symbol, pnl]) => (
                  <div key={symbol} style={{ fontSize: "0.9rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <span style={{ opacity: 0.6 }}>{symbol}:</span>
                    <span style={{ fontWeight: "600", color: pnl >= 0 ? "var(--gain)" : "var(--loss)" }}>
                      {pnl >= 0 ? "+" : ""}₹{pnl.toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
