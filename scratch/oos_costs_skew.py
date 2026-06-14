"""
Re-confirm the OOS +79k UNGATED NIFTY weekly condor under REALISTIC costs + vol skew.

Costs (per cycle, NIFTY lot=25):
  - Brokerage Rs20/order x 8 orders (4 legs in + 4 out) = Rs160 (flat).
  - Taxes (STT 0.1% sell + exchange ~0.035% + GST + stamp) ~= 0.12% of premium turnover.
  - SLIPPAGE (dominant): half bid-ask per leg, SLIP_PTS per leg x 8 fills x qty. Swept.
Skew: OTM puts price richer (put skew) -> PE legs use iv*SKEW_PE, CE legs iv*SKEW_CE.
  Net effect: more credit on the put side (helps the seller), strikes shift.

Sweeps SLIP_PTS x skew; reports UNGATED 4.5yr total vs the no-cost +79,009 baseline.
"""
import json
from datetime import date
from services.chartedge_core.option_data import bs_price, bs_delta

NIFTY_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412260601.txt"
VIX_F="/Users/nithish-prabhu/.claude/projects/-Users-nithish-prabhu-Downloads-intra-day/0110108b-9086-4c7e-8979-2b28631bac1b/tool-results/mcp-claude_ai_zerodha-get_historical_data-1780412267485.txt"

SHORT_DELTA=0.20; WING_DELTA=0.10; PROFIT_TAKE=0.55; STOP_MULT=2.2
RISK=0.02*500000; LOT=25; MAXLOTS=4; MIN_DTE=2
BROKERAGE=160.0; TAX_RATE=0.0012   # blended STT+exchange+GST+stamp on premium turnover

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

def run(SLIP_PTS, SKEW_PE, SKEW_CE):
    def legs_val(spot,dte,base_iv,st):
        sp,lp,sc,lc=st
        ivp=base_iv*SKEW_PE; ivc=base_iv*SKEW_CE
        short_pe=bs_price(spot,sp,dte,ivp,"PE"); long_pe=bs_price(spot,lp,dte,ivp,"PE")
        short_ce=bs_price(spot,sc,dte,ivc,"CE"); long_ce=bs_price(spot,lc,dte,ivc,"CE")
        net=(short_pe-long_pe)+(short_ce-long_ce)
        gross=short_pe+long_pe+short_ce+long_ce   # turnover proxy (all legs)
        return net, gross
    exps=[i for i,r in enumerate(rows) if r["d"].weekday()==1]
    tot=0.0; n=0; prev=-1
    for ei in exps:
        entry_i=prev+1; prev=ei
        if entry_i>=ei: continue
        if (rows[ei]["d"]-rows[entry_i]["d"]).days<MIN_DTE: continue
        spot0=rows[entry_i]["o"]; iv0=ann_iv(rows[entry_i]["vix"])
        ed=rows[ei]["d"]; dte0=max(1.0,(ed-rows[entry_i]["d"]).days+0.3)
        # strikes: PE picked at skewed PE-IV, CE at skewed CE-IV
        sp=pick(spot0,dte0,iv0*SKEW_PE,"PE",SHORT_DELTA,-1); lp=pick(spot0,dte0,iv0*SKEW_PE,"PE",WING_DELTA,-1)
        sc=pick(spot0,dte0,iv0*SKEW_CE,"CE",SHORT_DELTA,1);  lc=pick(spot0,dte0,iv0*SKEW_CE,"CE",WING_DELTA,1)
        st=(sp,lp,sc,lc)
        credit,gross0=legs_val(spot0,dte0,iv0,st)
        if credit<=1: continue
        maxloss=max(sp-lp,lc-sc)-credit
        if maxloss<=0: continue
        lots=max(1,min(int(RISK/(maxloss*LOT)),MAXLOTS)); qty=lots*LOT
        exitv=None; gexit=gross0
        for j in range(entry_i+1,ei+1):
            spot=rows[j]["c"]; iv_t=ann_iv(rows[j]["vix"]); dte_t=max(0.02,(ed-rows[j]["d"]).days+(0.3 if j!=ei else 0.0))
            v,gx=legs_val(spot,(0.0 if j==ei else dte_t),iv_t,st)
            if j==ei: exitv=min(v,maxloss+credit); gexit=gx; break
            if v<=(1-PROFIT_TAKE)*credit: exitv=v; gexit=gx; break
            if v>=STOP_MULT*credit: exitv=min(v,maxloss+credit); gexit=gx; break
        if exitv is None: continue
        slippage=SLIP_PTS*8*qty
        taxes=TAX_RATE*(gross0+gexit)*qty
        cost=slippage+BROKERAGE+taxes
        tot+=(credit-exitv)*qty-cost; n+=1
    return tot,n

print("\n==== OOS +79k under REAL costs + skew (NIFTY weekly, Jun'21-Nov'25, UNGATED) ====")
print(f"baseline (toy cost, flat IV) = +79,009")
print(f"{'slip/leg':>9} {'skew(PE/CE)':>12} | {'PnL':>9} | per-cycle")
for skew in [(1.00,1.00),(1.08,0.97)]:
    for slip in [0.0,0.5,1.0,1.5,2.0]:
        t,n=run(slip,skew[0],skew[1])
        sk="flat" if skew==(1.00,1.00) else "1.08/0.97"
        print(f"{slip:>9.1f} {sk:>12} | {t:>9.0f} | {t/max(1,n):>6.0f}  (n={n})")
