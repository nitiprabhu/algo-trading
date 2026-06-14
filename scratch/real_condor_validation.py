"""
REAL-DATA validation of the positional weekly iron condor.

Underlying NIFTY/BANKNIFTY + India VIX = REAL Zerodha daily history (Mar-May 2026),
NOT the runtime's synthetic path. Options still BS-priced (Zerodha purges expired-option
candles, so real option quotes for these cycles are unavailable), but vol = real VIX and
the spot path is ground-truth. Correct NIFTY weekly expiry = TUESDAY.

This checks the big doubt: my synthetic backtest showed March +11k, but REAL March was a
−10% (NIFTY) / −16% (BANKNIFTY) downtrend with VIX 17–28 — a put-condor should BLEED there.
"""
import math
from datetime import date
from services.chartedge_core.option_data import bs_price, bs_delta

# (date, nifty_open, nifty_close, bn_open, bn_close, vix_close)
DATA = [
 ("2026-03-02",24659.25,24865.70,59204.30,59839.65,17.13),("2026-03-04",24388.80,24480.50,58447.15,58755.25,21.14),
 ("2026-03-05",24615.95,24765.90,59008.25,59055.85,17.86),("2026-03-06",24656.40,24450.45,58629.60,57783.25,19.88),
 ("2026-03-09",23868.05,24028.05,56121.40,56019.80,23.36),("2026-03-10",24280.80,24261.60,56583.85,56950.80,18.91),
 ("2026-03-11",24231.85,23866.85,56790.40,55735.75,21.06),("2026-03-12",23674.85,23639.15,55008.20,55100.95,21.52),
 ("2026-03-13",23462.50,23151.10,54592.05,53757.85,22.65),("2026-03-16",23116.10,23408.80,53721.50,54413.40,21.60),
 ("2026-03-17",23493.20,23581.15,54649.10,54876.00,19.79),("2026-03-18",23632.90,23777.80,54927.05,55326.05,18.72),
 ("2026-03-19",23197.75,23002.15,53474.55,53451.00,22.80),("2026-03-20",23110.15,23114.50,53548.20,53427.05,22.81),
 ("2026-03-23",22824.35,22512.65,52576.10,51437.75,26.73),("2026-03-24",22878.45,22912.40,52384.80,52605.65,24.74),
 ("2026-03-25",23064.40,23306.45,53024.75,53708.10,24.64),("2026-03-27",23173.55,22819.60,53244.25,52274.60,26.80),
 ("2026-03-30",22549.65,22331.40,51527.90,50275.35,27.89),("2026-04-01",22899.00,22679.40,51433.90,51448.65,25.01),
 ("2026-04-02",22383.40,22713.10,50625.65,51548.75,25.52),("2026-04-06",22780.30,22968.25,51747.60,52609.10,25.47),
 ("2026-04-07",22838.70,23123.65,52258.70,52716.25,24.70),("2026-04-08",23855.15,23997.35,54904.45,55703.90,19.70),
 ("2026-04-09",23909.05,23775.10,55505.95,54821.70,20.43),("2026-04-10",23880.55,24050.60,55182.25,55912.75,18.85),
 ("2026-04-13",23589.60,23842.65,54646.00,55605.05,20.50),("2026-04-15",24163.80,24231.30,56343.45,56301.95,18.67),
 ("2026-04-16",24385.20,24196.75,56657.25,56086.40,18.09),("2026-04-17",24165.90,24353.55,56072.40,56565.70,17.21),
 ("2026-04-20",24391.50,24364.85,56704.05,56582.35,18.79),("2026-04-21",24374.55,24576.60,56823.60,57371.45,17.53),
 ("2026-04-22",24470.85,24378.10,57163.35,57124.45,18.30),("2026-04-23",24202.35,24173.05,56608.95,56305.00,18.59),
 ("2026-04-24",24100.55,23897.95,56170.20,56089.75,19.71),("2026-04-27",23945.45,24092.70,56162.60,56264.30,18.38),
 ("2026-04-28",24049.90,23995.70,55862.50,55400.35,18.05),("2026-04-29",24096.90,24177.65,55634.50,55403.60,17.44),
 ("2026-04-30",23996.95,23997.55,54880.65,54863.35,18.46),("2026-05-04",24063.55,24119.30,54937.90,54878.50,18.30),
 ("2026-05-05",24052.60,24032.80,54691.30,54547.05,17.91),("2026-05-06",24171.00,24330.95,55113.40,55981.05,16.68),
 ("2026-05-07",24398.50,24326.65,56114.00,56047.40,16.62),("2026-05-08",24233.65,24176.15,55783.95,55310.55,16.84),
 ("2026-05-11",23970.10,23815.85,54832.45,54439.90,18.55),("2026-05-12",23722.60,23379.55,54178.40,53555.20,19.28),
 ("2026-05-13",23362.45,23412.60,53600.40,53456.15,19.43),("2026-05-14",23530.25,23689.60,53639.50,54128.95,18.61),
 ("2026-05-15",23731.40,23643.50,54207.75,53710.35,18.79),("2026-05-18",23482.20,23649.95,53282.15,53537.00,19.63),
 ("2026-05-19",23675.30,23618.00,53553.75,53409.15,18.68),
]

SHORT_DELTA=0.20; WING_DELTA=0.10; PROFIT_TAKE=0.55; STOP_MULT=2.2
RISK_PER_TRADE=0.02*500000
LOT={"NIFTY":25,"BANKNIFTY":15}; MAXLOTS={"NIFTY":4,"BANKNIFTY":3}

def ann_iv(vix): return max(0.05, vix/100.0)
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

import calendar as _cal
rows=[{"d":date.fromisoformat(r[0]),"NIFTY":(r[1],r[2]),"BANKNIFTY":(r[3],r[4]),"vix":r[5]} for r in DATA]
dates=[r["d"] for r in rows]
idx_of={r["d"]:i for i,r in enumerate(rows)}

def snap(target):
    """Nearest trading day in data on/before target (handles holiday expiries)."""
    c=[d for d in dates if d<=target]
    return max(c) if c else None

# NIFTY weekly expiries = every Tuesday present. BANKNIFTY monthly = last Tuesday of each
# month (snapped to nearest trading day in data — real NSE rules).
nifty_exp=[d for d in dates if d.weekday()==1]
bn_exp=[]
for (y,m) in sorted({(d.year,d.month) for d in dates}):
    last=_cal.monthrange(y,m)[1]
    for day in range(last,last-7,-1):
        if date(y,m,day).weekday()==1:
            s=snap(date(y,m,day))
            if s and s not in bn_exp: bn_exp.append(s)
            break
bn_exp=sorted(bn_exp)

results={}; cyc=[]
for sym,exps in (("NIFTY",nifty_exp),("BANKNIFTY",bn_exp)):
    prev_i=-1
    for ed in exps:
        ei=idx_of[ed]
        entry_i=prev_i+1
        if entry_i>=ei:
            prev_i=ei; continue
        spot0=rows[entry_i][sym][0]; vix0=rows[entry_i]["vix"]; iv0=ann_iv(vix0)
        dte0=max(1.0,(ed-rows[entry_i]["d"]).days+0.3)
        sp=pick(spot0,dte0,iv0,"PE",SHORT_DELTA,-1); lp=pick(spot0,dte0,iv0,"PE",WING_DELTA,-1)
        sc=pick(spot0,dte0,iv0,"CE",SHORT_DELTA,1);  lc=pick(spot0,dte0,iv0,"CE",WING_DELTA,1)
        st=(sp,lp,sc,lc); credit=cval(spot0,dte0,iv0,st)
        if credit<=1: prev_i=ei; continue
        maxloss=max(sp-lp,lc-sc)-credit
        if maxloss<=0: prev_i=ei; continue
        lots=max(1,min(int(RISK_PER_TRADE/(maxloss*LOT[sym])),MAXLOTS[sym])); qty=lots*LOT[sym]
        cost=round(0.02*credit*qty+8*lots,2)
        exitv=None; why="EXPIRY"
        for j in range(entry_i+1,ei+1):
            spot=rows[j][sym][1]; iv_t=ann_iv(rows[j]["vix"])
            dte_t=max(0.02,(ed-rows[j]["d"]).days+(0.3 if j!=ei else 0.0))
            if j==ei:
                val=min(cval(spot,0.0,iv_t,st),maxloss+credit); exitv,why=val,"EXPIRY"; break
            val=cval(spot,dte_t,iv_t,st)
            if val<=(1-PROFIT_TAKE)*credit: exitv,why=val,"PT"; break
            if val>=STOP_MULT*credit: exitv,why=min(val,maxloss+credit),"STOP"; break
        if exitv is None: prev_i=ei; continue
        pnl=(credit-exitv)*qty-cost
        results[ed.month]=results.get(ed.month,0.0)+pnl
        cyc.append((str(rows[entry_i]["d"]),str(ed),sym,round(credit,1),round(exitv,1),why,round(pnl,0)))
        prev_i=ei
cyc.sort(key=lambda r:(r[0],r[2]))

print("\n==== REAL-DATA positional weekly condor (Tue expiry, real NIFTY/BN/VIX) ====")
print(f"{'entry':11} {'expiry':11} {'sym':9} {'cr':>6} {'exit':>6} {'why':6} {'pnl':>9}")
for r in cyc: print(f"{r[0]:11} {r[1]:11} {r[2]:9} {r[3]:>6} {r[4]:>6} {r[5]:6} {r[6]:>9.0f}")
w=[r[6] for r in cyc if r[6]>0]; l=[r[6] for r in cyc if r[6]<=0]
print("\n-- by month --")
for m in sorted(results): print(f"  {{3:'March',4:'April',5:'May'}}[{m}] = Rs {results[m]:>9.0f}".replace("{3:'March',4:'April',5:'May'}["+str(m)+"]",{3:'March',4:'April',5:'May'}[m]))
print(f"\nTOTAL: Rs {sum(results.values()):.0f}")
n=len(cyc)
if n: print(f"Cycles {n} | win {len(w)} ({len(w)/n*100:.0f}%) | avg win {sum(w)/len(w) if w else 0:.0f} | avg loss {sum(l)/len(l) if l else 0:.0f}")
print("BS-on-synthetic claimed: March +11,010 | April +19,373 | May -2,437 | +27,946")
