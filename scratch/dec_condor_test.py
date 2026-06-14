"""
Condor on a RANGING month: real Zerodha December 2025 (NIFTY/BANKNIFTY/VIX).
December was range-bound (NIFTY 25758-26205, ~1.7%; VIX 9-11) — the condor's natural
habitat, the opposite of the Mar-May crash. Tests whether premium-selling pays when
the market actually behaves. Same engine as real_condor_validation.py.
NIFTY weekly (Tue) expiries; BANKNIFTY monthly (last Tue = Dec 30).
"""
from datetime import date
import calendar as _cal
from services.chartedge_core.option_data import bs_price, bs_delta

# (date, nifty_open, nifty_close, bn_open, bn_close, vix)
DATA=[
 ("2025-12-01",26325.80,26175.75,60102.05,59681.35,11.63),("2025-12-02",26087.95,26032.20,59354.20,59273.80,11.23),
 ("2025-12-03",26004.90,25986.00,59158.70,59348.25,11.21),("2025-12-04",25981.85,26033.75,59287.10,59288.70,10.82),
 ("2025-12-05",25999.80,26186.45,59133.20,59777.20,10.32),("2025-12-08",26159.80,25960.55,59672.05,59238.55,11.13),
 ("2025-12-09",25867.10,25839.65,58918.85,59222.35,10.95),("2025-12-10",25864.05,25758.00,59281.55,58960.40,10.91),
 ("2025-12-11",25771.40,25898.55,58966.20,59209.85,10.40),("2025-12-12",25971.20,26046.95,59401.50,59389.95,10.11),
 ("2025-12-15",25930.05,26027.30,59053.70,59461.80,10.25),("2025-12-16",25951.50,25860.10,59288.75,59034.60,10.06),
 ("2025-12-17",25902.40,25818.55,59072.80,58926.75,9.84),("2025-12-18",25764.70,25815.55,58712.70,58912.85,9.71),
 ("2025-12-19",25911.50,25966.40,59047.40,59069.20,9.52),("2025-12-22",26055.85,26172.40,59224.75,59304.00,9.68),
 ("2025-12-23",26205.20,26177.15,59334.35,59299.55,9.38),("2025-12-24",26170.65,26142.10,59322.95,59183.60,9.19),
 ("2025-12-26",26121.25,26042.30,59092.85,59011.35,9.15),("2025-12-29",26063.35,25942.10,59007.05,58932.35,9.72),
 ("2025-12-30",25940.90,25938.85,58885.95,59171.25,9.68),("2025-12-31",25971.05,26129.60,59194.60,59581.85,9.48),
]
SHORT_DELTA=0.20; WING_DELTA=0.10; PROFIT_TAKE=0.55; STOP_MULT=2.2
RISK=0.02*500000; LOT={"NIFTY":25,"BANKNIFTY":15}; MAXLOTS={"NIFTY":4,"BANKNIFTY":3}
def ann_iv(v): return max(0.05,v/100.0)
def pick(spot,dte,iv,ot,td,dirn):
    step=50 if spot<40000 else 100; k=round(spot/step)*step
    for _ in range(150):
        k+=dirn*step
        if k<=0: break
        if abs(bs_delta(spot,k,dte,iv,ot))<=td: return k
    return k
def cval(spot,dte,iv,st):
    sp,lp,sc,lc=st
    return (bs_price(spot,sp,dte,iv,"PE")-bs_price(spot,lp,dte,iv,"PE"))+(bs_price(spot,sc,dte,iv,"CE")-bs_price(spot,lc,dte,iv,"CE"))

rows=[{"d":date.fromisoformat(r[0]),"NIFTY":(r[1],r[2]),"BANKNIFTY":(r[3],r[4]),"vix":r[5]} for r in DATA]
dates=[r["d"] for r in rows]; idx={r["d"]:i for i,r in enumerate(rows)}
def snap(t):
    c=[d for d in dates if d<=t]; return max(c) if c else None
nifty_exp=[d for d in dates if d.weekday()==1]
bn_exp=[]
for (y,m) in sorted({(d.year,d.month) for d in dates}):
    last=_cal.monthrange(y,m)[1]
    for day in range(last,last-7,-1):
        if date(y,m,day).weekday()==1:
            s=snap(date(y,m,day))
            if s and s not in bn_exp: bn_exp.append(s)
            break
results={}; cyc=[]
for sym,exps in (("NIFTY",nifty_exp),("BANKNIFTY",sorted(bn_exp))):
    prev=-1
    for ed in exps:
        ei=idx[ed]; entry_i=prev+1
        if entry_i>=ei: prev=ei; continue
        spot0=rows[entry_i][sym][0]; iv0=ann_iv(rows[entry_i]["vix"])
        dte0=max(1.0,(ed-rows[entry_i]["d"]).days+0.3)
        sp=pick(spot0,dte0,iv0,"PE",SHORT_DELTA,-1); lp=pick(spot0,dte0,iv0,"PE",WING_DELTA,-1)
        sc=pick(spot0,dte0,iv0,"CE",SHORT_DELTA,1);  lc=pick(spot0,dte0,iv0,"CE",WING_DELTA,1)
        st=(sp,lp,sc,lc); credit=cval(spot0,dte0,iv0,st)
        if credit<=1: prev=ei; continue
        maxloss=max(sp-lp,lc-sc)-credit
        if maxloss<=0: prev=ei; continue
        lots=max(1,min(int(RISK/(maxloss*LOT[sym])),MAXLOTS[sym])); qty=lots*LOT[sym]
        cost=round(0.02*credit*qty+8*lots,2)
        exitv=None; why="EXPIRY"
        for j in range(entry_i+1,ei+1):
            spot=rows[j][sym][1]; iv_t=ann_iv(rows[j]["vix"])
            dte_t=max(0.02,(ed-rows[j]["d"]).days+(0.3 if j!=ei else 0.0))
            if j==ei: exitv,why=min(cval(spot,0.0,iv_t,st),maxloss+credit),"EXPIRY"; break
            val=cval(spot,dte_t,iv_t,st)
            if val<=(1-PROFIT_TAKE)*credit: exitv,why=val,"PT"; break
            if val>=STOP_MULT*credit: exitv,why=min(val,maxloss+credit),"STOP"; break
        if exitv is None: prev=ei; continue
        pnl=(credit-exitv)*qty-cost
        results[sym]=results.get(sym,0.0)+pnl
        cyc.append((str(rows[entry_i]["d"]),str(ed),sym,round(credit,1),round(exitv,1),why,round(pnl,0)))
        prev=ei
cyc.sort(key=lambda r:(r[0],r[2]))
print("\n==== CONDOR on RANGING month — REAL December 2025 (VIX 9-11) ====")
print(f"{'entry':11} {'expiry':11} {'sym':9} {'cr':>6} {'exit':>6} {'why':6} {'pnl':>8}")
for r in cyc: print(f"{r[0]:11} {r[1]:11} {r[2]:9} {r[3]:>6} {r[4]:>6} {r[5]:6} {r[6]:>8.0f}")
tot=sum(results.values()); w=[r[6] for r in cyc if r[6]>0]
print("\n-- by index --")
for s in results: print(f"  {s:9}: Rs {results[s]:>8.0f}")
print(f"\nTOTAL December: Rs {tot:.0f} | cycles {len(cyc)} | win {len(w)}/{len(cyc)}")
print("Compare: condor on Mar-May (crash/trend) = -26,156")
