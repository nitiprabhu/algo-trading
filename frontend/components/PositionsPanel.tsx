import { BriefcaseBusiness } from "lucide-react";
import type { Trade } from "../lib/types";

export function PositionsPanel({ trades }: { trades: Trade[] }) {
  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Open Positions</h2>
        <BriefcaseBusiness size={18} />
      </div>
      <div className="stack">
        {trades.length === 0 ? <p className="empty">No open paper positions.</p> : null}
        {trades.map((trade) => (
          <div className="position" key={trade.id}>
            <div>
              <strong>{trade.instrument}</strong>
              <span className={`badge ${trade.direction.toLowerCase()}`}>{trade.direction}</span>
            </div>
            <dl>
              <div><dt>Qty</dt><dd>{trade.quantity}</dd></div>
              <div><dt>Invested</dt><dd>₹{trade.invested_amount.toLocaleString()}</dd></div>
              <div><dt>Entry</dt><dd>{trade.entry_price.toFixed(2)}</dd></div>
              <div><dt>SL</dt><dd>{trade.sl_price.toFixed(2)}</dd></div>
              <div><dt>T2</dt><dd>{trade.t2_price.toFixed(2)}</dd></div>
              <div><dt>P&L</dt><dd className={trade.pnl >= 0 ? "gain" : "loss"}>
                {trade.pnl >= 0 ? "+" : ""}{trade.pnl.toFixed(2)} ({trade.pnl_pct >= 0 ? "+" : ""}{trade.pnl_pct.toFixed(2)}%)
              </dd></div>
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}
