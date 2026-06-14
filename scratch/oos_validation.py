"""
OUT-OF-SAMPLE validation of the regime gate.

Gate was designed on Dec'25-May'26. This tests it on Jun 2021 - Nov 2025 (~4.5 years,
~54 months) of REAL Zerodha NIFTY + India VIX daily — none of which were used to design
the gate. SAME params, NO re-tuning. NIFTY-only weekly (Tue) iron condor.

Reads the two persisted MCP result files (no copy-paste).
"""
import json
from datetime import date
from services.chartedge_core.option_data import bs_price, bs_delta

NIFTY_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412260601.txt"
VIX_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412267485.txt"

# SAME gate + strategy params as gated_condor_test.py (NO re-tuning)
SHORT_DELTA=0.20; WING_DELTA=0.10; PROFIT_TAKE=0.55; STOP_MULT=2.2
RISK=0.02*500000; LOT=25; MAXLOTS=4
VIX_MAX=14.0; TREND_MAX=3.0; LOOKBACK=5; MIN_DTE=2

def ann_iv(v): return max(0.05,v/100.0)
def pick(spot,dte,iv,ot,td,dirn):
    step=50; k=round(spot/step)*step
    for _ in range(200):
        k+=dirn*step
        if k<=0: break
        if abs(bs_delta(spot,k,dte,iv,ot))<=td: return k
    return k
def cval(spot,dte,iv,st):
    sp,lp,sc,lc=st
    return (bs_price(spot,sp,dte,iv,"PE")-bs_price(spot,lp,dte,iv,"PE"))+(bs_price(spot,sc,dte,iv,"CE")-bs_price(spot,lc,dte,iv,"CE"))

nf=json.load(open(NIFTY_F)); vf=json.load(open(VIX_F))
vix_by={date.fromisoformat(r["date"][:10]):r["close"] for r in vf}
rows=[]
for r in nf:
    d=date.fromisoformat(r["date"][:10])
    rows.append({"d":d,"o":r["open"],"c":r["close"],"vix":vix_by.get(d)})
rows=[r for r in rows if r["vix"] is not None]
rows.sort(key=lambda r:r["d"])
idx={r["d"]:i for i,r in enumerate(rows)}

def trend_pct(ei):
    j=max(0,ei-LOOKBACK); a=rows[j]["c"]; b=rows[ei]["o"]
    return (b-a)/a*100.0 if a else 0.0

def run_cycle(entry_i,ei):
    spot0=rows[entry_i]["o"]; iv0=ann_iv(rows[entry_i]["vix"])
    ed=rows[ei]["d"]; dte0=max(1.0,(ed-rows[entry_i]["d"]).days+0.3)
    sp=pick(spot0,dte0,iv0,"PE",SHORT_DELTA,-1); lp=pick(spot0,dte0,iv0,"PE",WING_DELTA,-1)
    sc=pick(spot0,dte0,iv0,"CE",SHORT_DELTA,1);  lc=pick(spot0,dte0,iv0,"CE",WING_DELTA,1)
    st=(sp,lp,sc,lc); credit=cval(spot0,dte0,iv0,st)
    if credit<=1: return None
    maxloss=max(sp-lp,lc-sc)-credit
    if maxloss<=0: return None
    lots=max(1,min(int(RISK/(maxloss*LOT)),MAXLOTS)); qty=lots*LOT
    cost=round(0.02*credit*qty+8*lots,2)
    exitv=None
    for j in range(entry_i+1,ei+1):
        spot=rows[j]["c"]; iv_t=ann_iv(rows[j]["vix"]); dte_t=max(0.02,(ed-rows[j]["d"]).days+(0.3 if j!=ei else 0.0))
        if j==ei: exitv=min(cval(spot,0.0,iv_t,st),maxloss+credit); break
        val=cval(spot,dte_t,iv_t,st)
        if val<=(1-PROFIT_TAKE)*credit: exitv=val; break
        if val>=STOP_MULT*credit: exitv=min(val,maxloss+credit); break
    if exitv is None: return None
    return (credit-exitv)*qty-cost

# NIFTY weekly Tue expiries; cycles entry=first day after prev expiry
exps=[i for i,r in enumerate(rows) if r["d"].weekday()==1]
ug=g=0.0; n_ug=n_g=0; gw=0
calm_pnl=vol_pnl=0.0; calm_n=vol_n=0; per_year={}
prev=-1
for ei in exps:
    entry_i=prev+1
    if entry_i>=ei: prev=ei; continue
    if (rows[ei]["d"]-rows[entry_i]["d"]).days<MIN_DTE: prev=ei; continue
    pnl=run_cycle(entry_i,ei)
    prev=ei
    if pnl is None: continue
    ug+=pnl; n_ug+=1
    vix=rows[entry_i]["vix"]; tr=abs(trend_pct(entry_i))
    yr=rows[ei]["d"].year; per_year.setdefault(yr,[0.0,0.0])
    per_year[yr][0]+=pnl
    if vix<=VIX_MAX: calm_pnl+=pnl; calm_n+=1
    else: vol_pnl+=pnl; vol_n+=1
    if vix<=VIX_MAX and tr<=TREND_MAX:
        g+=pnl; n_g+=1; gw+= (pnl>0); per_year[yr][1]+=pnl

print("\n==== OUT-OF-SAMPLE gate validation — NIFTY weekly condor, Jun'21-Nov'25 ====")
print(f"gate (UNCHANGED): VIX<={VIX_MAX}, |{LOOKBACK}d trend|<={TREND_MAX}%, skip <{MIN_DTE}DTE")
print(f"\nUNGATED (sell every week): Rs {ug:>9.0f} over {n_ug} cycles")
print(f"GATED   (sell only calm):  Rs {g:>9.0f} over {n_g} cycles | win {gw}/{n_g} ({100*gw/max(1,n_g):.0f}%)")
print(f"\nPremise check (ungated PnL by regime at entry):")
print(f"  calm  (VIX<=14): Rs {calm_pnl:>9.0f} over {calm_n} cycles  (avg {calm_pnl/max(1,calm_n):>6.0f})")
print(f"  vol   (VIX> 14): Rs {vol_pnl:>9.0f} over {vol_n} cycles  (avg {vol_pnl/max(1,vol_n):>6.0f})")
print("\nPer-year (ungated | gated):")
for y in sorted(per_year):
    print(f"  {y}: {per_year[y][0]:>9.0f} | {per_year[y][1]:>9.0f}")
