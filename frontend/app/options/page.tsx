"use client";

import { useState, useEffect } from "react";
import { ArrowLeft, Calculator, TrendingUp, Zap, Shield, Target, Clock, Activity } from "lucide-react";
import Link from "next/link";
import { PayoffChart } from "../../components/PayoffChart";
import { DashboardSnapshot, Signal, Trade } from "../../lib/types";

type Strategy = "LONG_CALL" | "LONG_PUT" | "BULL_CALL_SPREAD" | "BEAR_PUT_SPREAD" | "IRON_CONDOR";

export default function OptionsPage() {
  const [spotPrice, setSpotPrice] = useState(24000);
  const [strategy, setStrategy] = useState<Strategy>("LONG_CALL");
  const [strikes, setStrikes] = useState<number[]>([24000]);
  const [premiums, setPremiums] = useState<number[]>([150]);
  const [types, setTypes] = useState<("CE" | "PE")[]>(["CE"]);
  const [directions, setDirections] = useState<("BUY" | "SELL")[]>(["BUY"]);
  
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  // Sync strategy presets
  useEffect(() => {
    switch (strategy) {
      case "LONG_CALL":
        setStrikes([spotPrice]);
        setPremiums([150]);
        setTypes(["CE"]);
        setDirections(["BUY"]);
        break;
      case "LONG_PUT":
        setStrikes([spotPrice]);
        setPremiums([150]);
        setTypes(["PE"]);
        setDirections(["BUY"]);
        break;
      case "BULL_CALL_SPREAD":
        setStrikes([spotPrice, spotPrice + 200]);
        setPremiums([200, 80]);
        setTypes(["CE", "CE"]);
        setDirections(["BUY", "SELL"]);
        break;
      case "BEAR_PUT_SPREAD":
        setStrikes([spotPrice, spotPrice - 200]);
        setPremiums([200, 80]);
        setTypes(["PE", "PE"]);
        setDirections(["BUY", "SELL"]);
        break;
      case "IRON_CONDOR":
        setStrikes([spotPrice - 300, spotPrice - 100, spotPrice + 100, spotPrice + 300]);
        setPremiums([50, 150, 150, 50]);
        setTypes(["PE", "PE", "CE", "CE"]);
        setDirections(["BUY", "SELL", "SELL", "BUY"]);
        break;
    }
  }, [strategy, spotPrice]);

  // Live data fetch
  useEffect(() => {
    const fetchSnapshot = async () => {
      try {
        const res = await fetch(`${API_URL}/api/snapshot`);
        const data = await res.json();
        setSnapshot(data);
        if (data.latest_indicators?.NIFTY) {
          setSpotPrice(data.market_data_history?.NIFTY?.slice(-1)[0]?.price || spotPrice);
        }
      } catch (e) {
        console.error("Failed to fetch snapshot", e);
      }
    };

    fetchSnapshot();
    const interval = setInterval(fetchSnapshot, 2000);
    return () => clearInterval(interval);
  }, []);

  const foSignals = snapshot?.signals.filter(s => s.strategy_name && s.strategy_name !== "CONFLUENCE") || [];
  const openFOPositions = snapshot?.open_positions.filter(p => p.option_type) || [];

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">F&O Analytics & Strategy Lab</p>
          <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
            <Link href="/" className="nav-tab" style={{ display: "flex", alignItems: "center", gap: "8px", padding: "4px 8px" }}>
              <ArrowLeft size={16} /> Dashboard
            </Link>
            <h1>Options Earning Potential</h1>
          </div>
        </div>
        <div className="actions" style={{ display: "flex", gap: "12px" }}>
          <div className="glass" style={{ padding: "8px 16px", borderRadius: "20px", display: "flex", alignItems: "center", gap: "12px" }}>
             <span style={{ fontSize: "12px", color: "var(--muted)" }}>NIFTY SPOT</span>
             <strong style={{ color: "var(--green)" }}>₹{spotPrice.toLocaleString()}</strong>
          </div>
          <div className="glass" style={{ padding: "8px 16px", borderRadius: "20px", display: "flex", alignItems: "center", gap: "12px" }}>
             <span style={{ fontSize: "12px", color: "var(--muted)" }}>INDIA VIX</span>
             <strong style={{ color: "var(--amber)" }}>{(snapshot?.latest_indicators?.INDIAVIX?.indicators?.atr?.value as string | number) || "12.5"}</strong>
          </div>
        </div>
      </header>

      <div className="options-grid">
        <aside className="stack">
          {/* Live Signals Section */}
          <section className="panel glass" style={{ padding: "1.25rem", borderLeft: "4px solid var(--accent)" }}>
            <h2 style={{ marginBottom: "1rem", display: "flex", alignItems: "center", gap: "8px", fontSize: "16px" }}>
              <Zap size={18} color="var(--accent)" /> Live Quant Triggers
            </h2>
            <div className="stack" style={{ gap: "8px" }}>
              {foSignals.length === 0 ? (
                <p style={{ fontSize: "12px", color: "var(--muted)", textAlign: "center", padding: "20px" }}>Monitoring ORB & 5EMA...</p>
              ) : (
                foSignals.slice(0, 3).map(sig => (
                  <div key={sig.id} className="glass" style={{ padding: "10px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.05)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                      <span style={{ fontWeight: "bold", fontSize: "13px" }}>{sig.strategy_name}</span>
                      <span className="gain" style={{ fontSize: "11px" }}>{sig.option_type} BUY</span>
                    </div>
                    <p style={{ fontSize: "11px", color: "var(--muted)" }}>{sig.reasoning}</p>
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="panel glass strategy-card">
            <h2 style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", gap: "8px" }}>
              <Calculator size={18} color="var(--accent)" /> Strategy Lab Controls
            </h2>
            
            <div className="input-group">
              <label>Select Strategy</label>
              <select value={strategy} onChange={(e) => setStrategy(e.target.value as Strategy)}>
                <option value="LONG_CALL">Long Call</option>
                <option value="LONG_PUT">Long Put</option>
                <option value="BULL_CALL_SPREAD">Bull Call Spread</option>
                <option value="BEAR_PUT_SPREAD">Bear Put Spread</option>
                <option value="IRON_CONDOR">Iron Condor</option>
              </select>
            </div>

            <div className="input-group">
              <label>Manual Spot Entry</label>
              <input type="number" value={spotPrice} onChange={(e) => setSpotPrice(Number(e.target.value))} />
            </div>

            <hr style={{ border: "none", borderTop: "1px solid var(--line)", margin: "1.5rem 0" }} />
            
            {strikes.map((strike, i) => (
              <div key={i} className="position glass" style={{ marginBottom: "1rem", padding: "12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                  <span style={{ fontSize: "12px", fontWeight: "bold", color: directions[i] === "BUY" ? "var(--green)" : "var(--red)" }}>
                    {directions[i]} {types[i]}
                  </span>
                  <span style={{ fontSize: "10px", color: "var(--muted)" }}>Leg {i + 1}</span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label>Strike</label>
                    <input type="number" value={strike} onChange={(e) => {
                      const newStrikes = [...strikes];
                      newStrikes[i] = Number(e.target.value);
                      setStrikes(newStrikes);
                    }} />
                  </div>
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label>Premium</label>
                    <input type="number" value={premiums[i]} onChange={(e) => {
                      const newPremiums = [...premiums];
                      newPremiums[i] = Number(e.target.value);
                      setPremiums(newPremiums);
                    }} />
                  </div>
                </div>
              </div>
            ))}
          </section>
        </aside>

        <section className="stack">
          {/* Active Positions & Theta Decay warnings */}
          {openFOPositions.length > 0 && (
            <section className="panel glass" style={{ padding: "1.25rem", border: "1px solid var(--red)" }}>
              <h2 style={{ marginBottom: "1rem", display: "flex", alignItems: "center", gap: "8px", color: "var(--red)" }}>
                <Activity size={18} /> High-Risk Active Trades
              </h2>
              <div className="positions-list">
                {openFOPositions.map(pos => (
                  <div key={pos.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px", borderBottom: "1px solid var(--line)" }}>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <span style={{ fontWeight: "bold" }}>NIFTY {pos.option_type} ATM</span>
                        <span className={pos.pnl >= 0 ? "gain" : "loss"} style={{ fontSize: "12px" }}>
                          {pos.pnl >= 0 ? "+" : ""}{pos.pnl_pct}%
                        </span>
                      </div>
                      <div style={{ display: "flex", gap: "12px", marginTop: "4px" }}>
                        <span style={{ fontSize: "10px", color: "var(--muted)", display: "flex", alignItems: "center", gap: "4px" }}>
                          <Clock size={10} /> {Math.floor((Date.now() - new Date(pos.entry_time).getTime()) / 60000)}m in trade
                        </span>
                        {Math.floor((Date.now() - new Date(pos.entry_time).getTime()) / 60000) > 20 && (
                          <span style={{ fontSize: "10px", color: "var(--amber)", fontWeight: "bold" }}>⚠️ THETA RISK HIGH</span>
                        )}
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <p style={{ fontSize: "14px", fontWeight: "bold" }}>₹{pos.pnl.toLocaleString()}</p>
                      <p style={{ fontSize: "10px", color: "var(--muted)" }}>LTP: ₹{pos.entry_price + (pos.pnl / pos.quantity)}</p>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="panel glass" style={{ padding: "1.5rem" }}>
             <div className="panelHeader">
                <h2>Payoff Diagram (At Expiration)</h2>
                <div style={{ display: "flex", gap: "1rem" }}>
                   <span className="profit-text" style={{ fontSize: "12px" }}>● Profit Zone</span>
                   <span className="loss-text" style={{ fontSize: "12px" }}>● Loss Zone</span>
                </div>
             </div>
             <PayoffChart 
                spotPrice={spotPrice} 
                strikes={strikes} 
                premiums={premiums} 
                types={types} 
                directions={directions} 
             />
          </section>

          <section className="panel glass" style={{ padding: "1.5rem" }}>
             <h2 style={{ marginBottom: "1rem", display: "flex", alignItems: "center", gap: "8px" }}>
                <Target size={18} color="var(--accent)" /> Live Option Chain (NIFTY)
             </h2>
             <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
                   <thead>
                      <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--muted)" }}>
                         <th style={{ textAlign: "left", padding: "8px" }}>CALL (CE)</th>
                         <th style={{ textAlign: "center", padding: "8px" }}>STRIKE</th>
                         <th style={{ textAlign: "right", padding: "8px" }}>PUT (PE)</th>
                      </tr>
                   </thead>
                   <tbody>
                      {snapshot?.latest_indicators?.NIFTY?.options_data?.chain?.map((row, idx) => (
                         <tr key={idx} style={{ borderBottom: "1px solid rgba(255,255,255,0.02)", background: row.strike === (snapshot?.latest_indicators?.NIFTY?.options_data?.chain[Math.floor(snapshot.latest_indicators.NIFTY.options_data.chain.length/2)].strike) ? "rgba(255,255,255,0.03)" : "transparent" }}>
                            <td style={{ padding: "8px" }}>
                               <button 
                                 onClick={() => {
                                   setStrikes([row.strike]);
                                   setTypes(["CE"]);
                                 }}
                                 style={{ background: "transparent", border: "1px solid var(--green)", color: "var(--green)", padding: "2px 8px", borderRadius: "4px", cursor: "pointer", fontSize: "10px" }}
                               >
                                 BUY CE
                               </button>
                            </td>
                            <td style={{ textAlign: "center", padding: "8px", fontWeight: "bold", background: "rgba(0,0,0,0.2)" }}>
                               {row.strike}
                            </td>
                            <td style={{ textAlign: "right", padding: "8px" }}>
                               <button 
                                 onClick={() => {
                                   setStrikes([row.strike]);
                                   setTypes(["PE"]);
                                 }}
                                 style={{ background: "transparent", border: "1px solid var(--red)", color: "var(--red)", padding: "2px 8px", borderRadius: "4px", cursor: "pointer", fontSize: "10px" }}
                               >
                                 BUY PE
                               </button>
                            </td>
                         </tr>
                      ))}
                   </tbody>
                </table>
             </div>
          </section>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "14px" }}>
             <SummaryCard 
                icon={<TrendingUp size={20} color="var(--green)" />} 
                label="Max Profit" 
                value="Unlimited" 
                tone="gain"
             />
             <SummaryCard 
                icon={<Shield size={20} color="var(--red)" />} 
                label="Max Loss" 
                value={`₹${(premiums.reduce((a, b) => a + b, 0) * 50).toLocaleString()}`} 
                tone="loss"
             />
             <SummaryCard 
                icon={<Target size={20} color="var(--blue)" />} 
                label="Break Even" 
                value={`₹${(strikes[0] + (types[0] === "CE" ? premiums[0] : -premiums[0])).toLocaleString()}`} 
             />
          </div>

          <section className="panel glass" style={{ padding: "1.5rem" }}>
             <h2 style={{ marginBottom: "1rem", display: "flex", alignItems: "center", gap: "8px" }}>
                <Zap size={18} color="var(--amber)" /> AI Option Insights
             </h2>
             <p style={{ color: "var(--muted)", fontSize: "14px", lineHeight: "1.6" }}>
                Market context is currently {snapshot?.latest_indicators?.NIFTY?.higher_timeframe?.["1D"] === "UP" ? "bullish" : "bearish"} on the daily timeframe. 
                Nifty basis is {snapshot?.latest_indicators?.NIFTY?.market_context?.basis || "0.0"}, suggesting {Math.abs(snapshot?.latest_indicators?.NIFTY?.market_context?.basis || 0) > 10 ? "strong futures activity" : "balanced activity"}. 
                Resistance Wall (Max Call OI) at <strong>{snapshot?.latest_indicators?.NIFTY?.options_data?.resistance_wall || "N/A"}</strong> and Support Wall at <strong>{snapshot?.latest_indicators?.NIFTY?.options_data?.support_wall || "N/A"}</strong>.
             </p>
          </section>
        </section>
      </div>
    </main>
  );
}

function GreekBox({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: "rgba(255,255,255,0.03)", padding: "10px", borderRadius: "6px", border: "1px solid var(--line)" }}>
       <span style={{ fontSize: "11px", color: "var(--muted)", display: "block" }}>{label}</span>
       <strong style={{ fontSize: "14px" }}>{value}</strong>
    </div>
  );
}

function SummaryCard({ icon, label, value, tone }: { icon: any; label: string; value: string; tone?: "gain" | "loss" }) {
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
