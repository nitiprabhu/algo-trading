"""
2-LEG credit spread vs the 4-leg condor — does halving the bid-ask legs save the edge?
Real Zerodha NIFTY+VIX Jun'21-Nov'25, weekly Tue, real costs + put-skew, slippage swept.
A spread crosses the spread 4x/cycle (2 legs x in+out) vs 8x for a condor.

Variants:
  PUT  = bull-put spread (sell 0.20d PE, buy 0.10d PE) every week  [rides upward drift]
  CALL = bear-call spread (sell 0.20d CE, buy 0.10d CE) every week
  TREND= sell PUT-spread if last 5d up, else CALL-spread (trend-aligned premium)
"""
import json
from datetime import date
from services.chartedge_core.option_data import bs_price, bs_delta

NIFTY_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412260601.txt"
VIX_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412267485.txt"

SHORT_DELTA=0.20; WING_DELTA=0.10; PROFIT_TAKE=0.55; STOP_MULT=2.2
RISK=0.02*500000; LOT=25; MAXLOTS=4; MIN_DTE=2
BROKERAGE=80.0; TAX_RATE=0.0012   # 2 legs x in+out = 4 orders x Rs20
SKEW_PE=1.08; SKEW_CE=0.97

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

def spval(spot,dte,base_iv,side,s,l):
    iv=base_iv*(SKEW_PE if side=="PE" else SKEW_CE)
    sh=bs_price(spot,s,dte,iv,side); lo=bs_price(spot,l,dte,iv,side)
    return sh-lo, sh+lo

def run(SLIP, variant):
    exps=[i for i,r in enumerate(rows) if r["d"].weekday()==1]
    tot=0.0; n=0; prev=-1
    for ei in exps:
        entry_i=prev+1; prev=ei
        if entry_i>=ei: continue
        if (rows[ei]["d"]-rows[entry_i]["d"]).days<MIN_DTE: continue
        spot0=rows[entry_i]["o"]; iv0=ann_iv(rows[entry_i]["vix"]); ed=rows[ei]["d"]
        dte0=max(1.0,(ed-rows[entry_i]["d"]).days+0.3)
        # choose side
        if variant=="PUT": side="PE"
        elif variant=="CALL": side="CE"
        else:
            j=max(0,entry_i-5); drift=rows[entry_i]["o"]-rows[j]["c"]
            side="PE" if drift>=0 else "CE"
        if side=="PE":
            s=pick(spot0,dte0,iv0*SKEW_PE,"PE",SHORT_DELTA,-1); l=pick(spot0,dte0,iv0*SKEW_PE,"PE",WING_DELTA,-1)
            width=s-l
        else:
            s=pick(spot0,dte0,iv0*SKEW_CE,"CE",SHORT_DELTA,1); l=pick(spot0,dte0,iv0*SKEW_CE,"CE",WING_DELTA,1)
            width=l-s
        credit,gross0=spval(spot0,dte0,iv0,side,s,l)
        if credit<=1 or width<=0: continue
        maxloss=width-credit
        if maxloss<=0: continue
        lots=max(1,min(int(RISK/(maxloss*LOT)),MAXLOTS)); qty=lots*LOT
        exitv=None; gx=gross0
        for j in range(entry_i+1,ei+1):
            spot=rows[j]["c"]; iv_t=ann_iv(rows[j]["vix"]); dte_t=max(0.02,(ed-rows[j]["d"]).days+(0.3 if j!=ei else 0.0))
            v,g=spval(spot,(0.0 if j==ei else dte_t),iv_t,side,s,l)
            if j==ei: exitv=min(v,maxloss+credit); gx=g; break
            if v<=(1-PROFIT_TAKE)*credit: exitv=v; gx=g; break
            if v>=STOP_MULT*credit: exitv=min(v,maxloss+credit); gx=g; break
        if exitv is None: continue
        cost=SLIP*4*qty + BROKERAGE + TAX_RATE*(gross0+gx)*qty
        tot+=(credit-exitv)*qty-cost; n+=1
    return tot,n

print("\n==== 2-LEG credit spread, real costs+skew, NIFTY weekly Jun'21-Nov'25 ====")
print("(4-leg condor for ref: 0slip +64k | 0.5 +11k | 1.0 -41k)")
print(f"{'variant':6} {'slip0':>8} {'slip0.5':>8} {'slip1.0':>8} {'slip1.5':>8}")
for v in ("PUT","CALL","TREND"):
    line=f"{v:6}"
    for slip in (0.0,0.5,1.0,1.5):
        t,n=run(slip,v); line+=f" {t:>8.0f}"
    print(line + f"   (n={n})")
