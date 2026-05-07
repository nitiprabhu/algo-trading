import { Gauge } from "lucide-react";
import type { IndicatorSnapshot } from "../lib/types";

export function IndicatorPanel({ snapshots }: { snapshots: Record<string, IndicatorSnapshot> }) {
  return (
    <section className="panel span2">
      <div className="panelHeader">
        <h2>Indicator Confluence</h2>
        <Gauge size={18} />
      </div>
      <div className="indicatorGrid">
        {Object.values(snapshots).map((snapshot) => (
          <div className="indicatorGroup" key={snapshot.instrument}>
            <div className="instrumentLine">
              <strong>{snapshot.instrument}</strong>
              <span className={snapshot.confluence_score >= 0 ? "gain" : "loss"}>
                {snapshot.confluence_score.toFixed(2)}
              </span>
            </div>
            {Object.entries(snapshot.indicators).slice(0, 6).map(([name, indicator]) => (
              <div className="indicator" key={name}>
                <span>{name.replace("_", " ")}</span>
                <span>{indicator.state}</span>
                <meter min={-1} max={1} value={indicator.vote} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
