import { Activity, CircleDot } from "lucide-react";
import type { Signal } from "../lib/types";

export function SignalFeed({ signals }: { signals: Signal[] }) {
  return (
    <section className="panel span2">
      <div className="panelHeader">
        <h2>Live Signal Feed</h2>
        <Activity size={18} />
      </div>
      <div className="table">
        <div className="row header">
          <span>Time</span>
          <span>Instrument</span>
          <span>Signal</span>
          <span>Confidence</span>
          <span>Plan</span>
        </div>
        {signals.slice(0, 12).map((signal) => (
          <div className="row" key={signal.id}>
            <span>
              {(() => {
                const d = typeof signal.created_at === 'string' && !signal.created_at.includes('Z') && !signal.created_at.includes('+') 
                  ? new Date(signal.created_at + 'Z') 
                  : new Date(signal.created_at);
                return d.toLocaleTimeString("en-IN", { timeZone: 'Asia/Kolkata', hour: "2-digit", minute: "2-digit", hour12: false });
              })()}
            </span>
            <span>{signal.instrument}</span>
            <span className={`badge ${signal.signal.toLowerCase()}`}>
              <CircleDot size={12} /> {signal.signal}
            </span>
            <span>{signal.confidence}%</span>
            <span>
              {signal.entry_zone.low.toFixed(1)}-{signal.entry_zone.high.toFixed(1)} / SL {signal.stop_loss.toFixed(1)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
