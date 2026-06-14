"""
EXECUTION-SCENARIO P&L: does limit-at-mid execution flip the edge profitable?

Anchored to the LIVE measured market half-spread (~0.34 pt/leg, Jun 2026). Limit orders
capture part of the spread (web-confirmed: ~half), so effective slippage = (1-capture)*spread.
Runs the OOS NIFTY weekly condor (8 legs) AND trend-aligned 2-leg spread across realistic
execution scenarios, with put-skew + real taxes. 4.5yr real Zerodha data.
"""
import json
from datetime import date
from services.chartedge_core.option_data import bs_price, bs_delta

NIFTY_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412260601.txt"
VIX_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412267485.txt"

SHORT_DELTA=0.20; WING_DELTA=0.10; PROFIT_TAKE=0.55; STOP_MULT=2.2
RISK=0.02*500000; LOT=65; MAXLOTS=2; MIN_DTE=2; TAX_RATE=0.0012
SKEW_PE=1.08; SKEW_CE=0.97
MKT_HALF=0.34   # live-measured market half-spread per leg (Jun-2026 snapshot)

def ann_iv(v): return max(0.05,v/100.0)
def pick(spot,dte,iv,ot,td,dirn):
    step=50; k=round(spot/step)*step
    for _ in range(200):
        k+=dirn*step
        if k<=0: break
        if abs(bs_delta(spot,k,dte,iv,ot))<=td: return k
    return k
nf=json.load(open(NIFTY_F)); vf=json.load(open(VIX_F))
vix_by={date.fromisoformat(r["date"][:10]):r["close"] for r in vf}
rows=[{"d":date.fromisoformat(r["date"][:10]),"o":r["open"],"c":r["close"],"vix":vix_by.get(date.fromisoformat(r["date"][:10]))} for r in nf]
rows=[r for r in rows if r["vix"] is not None]; rows.sort(key=lambda r:r["d"])
exps=[i for i,r in enumerate(rows) if r["d"].weekday()==1]

def price(spot,dte,base_iv,side,s,l):
    iv=base_iv*(SKEW_PE if side=="PE" else SKEW_CE)
    sh=bs_price(spot,s,dte,iv,side); lo=bs_price(spot,l,dte,iv,side)
    return sh-lo, sh+lo

def run(struct, SLIP):
    BROK = 160.0 if struct=="condor" else 80.0
    NLEG = 8 if struct=="condor" else 4
    tot=0.0; prev=-1
    for ei in exps:
        entry_i=prev+1; prev=ei
        if entry_i>=ei or (rows[ei]["d"]-rows[entry_i]["d"]).days<MIN_DTE: continue
        spot0=rows[entry_i]["o"]; iv0=ann_iv(rows[entry_i]["vix"]); ed=rows[ei]["d"]
        dte0=max(1.0,(ed-rows[entry_i]["d"]).days+0.3)
        if struct=="condor": sides=["PE","CE"]
        else:
            j=max(0,entry_i-5); sides=["PE"] if rows[entry_i]["o"]-rows[j]["c"]>=0 else ["CE"]
        credit=0.0; gross0=0.0; maxloss=0.0; book=[]
        for side in sides:
            sk=SKEW_PE if side=="PE" else SKEW_CE
            if side=="PE": s=pick(spot0,dte0,iv0*sk,"PE",SHORT_DELTA,-1); l=pick(spot0,dte0,iv0*sk,"PE",WING_DELTA,-1); w=s-l
            else: s=pick(spot0,dte0,iv0*sk,"CE",SHORT_DELTA,1); l=pick(spot0,dte0,iv0*sk,"CE",WING_DELTA,1); w=l-s
            c,g=price(spot0,dte0,iv0,side,s,l)
            if c<=0 or w<=0: book=[]; break
            credit+=c; gross0+=g; maxloss=max(maxloss,w); book.append((side,s,l))
        if not book or credit<=1: continue
        net_maxloss=maxloss-credit
        if net_maxloss<=0: continue
        lots=max(1,min(int(RISK/(net_maxloss*LOT)),MAXLOTS)); qty=lots*LOT
        exitv=None; gx=gross0
        for j in range(entry_i+1,ei+1):
            spot=rows[j]["c"]; iv_t=ann_iv(rows[j]["vix"]); dte_t=max(0.02,(ed-rows[j]["d"]).days+(0.3 if j!=ei else 0.0))
            v=0.0; g=0.0
            for side,s,l in book:
                vv,gg=price(spot,(0.0 if j==ei else dte_t),iv_t,side,s,l); v+=vv; g+=gg
            if j==ei: exitv=min(v,net_maxloss+credit); gx=g; break
            if v<=(1-PROFIT_TAKE)*credit: exitv=v; gx=g; break
            if v>=STOP_MULT*credit: exitv=min(v,net_maxloss+credit); gx=g; break
        if exitv is None: continue
        cost=SLIP*NLEG*qty + BROK + TAX_RATE*(gross0+gx)*qty
        tot+=(credit-exitv)*qty-cost
    return tot

print("\n==== EXECUTION SCENARIOS — does limit-at-mid flip it? (4.5yr real, skew+tax) ====")
print(f"live-measured market half-spread = {MKT_HALF}pt/leg")
scen=[("market order (0% capture)",MKT_HALF*1.00),
      ("limit, 30% capture",       MKT_HALF*0.70),
      ("limit-at-mid, 50% capture", MKT_HALF*0.50),
      ("limit, 70% capture",       MKT_HALF*0.30),
      ("pessimistic intraday/size", 0.55)]
print(f"\n{'scenario':28} {'slip/leg':>8} | {'CONDOR':>9} | {'TREND-SPREAD':>12}")
for name,slip in scen:
    print(f"{name:28} {slip:>8.2f} | {run('condor',slip):>9.0f} | {run('spread',slip):>12.0f}")
