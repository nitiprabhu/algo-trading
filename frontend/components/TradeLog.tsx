import { History } from "lucide-react";
import type { Trade } from "../lib/types";

export function TradeLog({ trades }: { trades: Trade[] }) {
  return (
    <section className="panel span2">
      <div className="panelHeader">
        <h2>Trade Log</h2>
        <History size={18} />
      </div>
      <div className="table compact">
        <div className="row header">
          <span>Time</span>
          <span>Instrument</span>
          <span>Side</span>
          <span>Qty</span>
          <span>Entry</span>
          <span>Exit</span>
          <span>Invested</span>
          <span>P&L</span>
          <span>% P&L</span>
        </div>
        {trades.slice().reverse().slice(0, 10).map((trade) => (
          <div className="row" key={trade.id}>
            <span style={{ fontSize: '0.75rem', opacity: 0.6 }}>
              {(() => {
                const dateStr = trade.exit_time || trade.entry_time;
                const d = typeof dateStr === 'string' && !dateStr.includes('Z') && !dateStr.includes('+') 
                  ? new Date(dateStr + 'Z') 
                  : new Date(dateStr);
                return d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false });
              })()}
            </span>
            <span>{trade.instrument}</span>
            <span style={{ fontSize: '0.75rem', opacity: 0.8 }}>{trade.direction}</span>
            <span>{trade.quantity}</span>
            <span>{trade.entry_price.toFixed(2)}</span>
            <span>{trade.exit_price?.toFixed(2) ?? "-"}</span>
            <span style={{ opacity: 0.7 }}>₹{trade.invested_amount.toLocaleString()}</span>
            <span className={trade.pnl >= 0 ? "gain" : "loss"}>{trade.pnl >= 0 ? "+" : ""}{trade.pnl.toFixed(2)}</span>
            <span className={trade.pnl >= 0 ? "gain" : "loss"} style={{ fontSize: '0.8rem' }}>
              {trade.pnl_pct >= 0 ? "+" : ""}{trade.pnl_pct.toFixed(2)}%
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
