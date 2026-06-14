"""
REGIME-GATED positional condor on REAL Zerodha data across 5 months / all regimes.

Gate (decided at each cycle's ENTRY, no lookahead): SELL the condor only when the
market is range-bound — VIX <= VIX_MAX AND |recent trend| <= TREND_MAX over the prior
LOOKBACK days. Otherwise STAND ASIDE. Also skip degenerate <MIN_DTE cycles.

Months (real daily NIFTY/BANKNIFTY/VIX): Dec'25 (range), Jan'26 (mixed down→up),
Mar/Apr/May'26 (crash/downtrend/chop). NIFTY weekly Tue expiry; BANKNIFTY monthly.

Shows UNGATED (trade every cycle) vs GATED (trade only range-confirmed) per month.
"""
from datetime import date
import calendar as _cal
from services.chartedge_core.option_data import bs_price, bs_delta

# (date, n_open, n_close, bn_open, bn_close, vix)
DATA=[
 # --- December 2025 (RANGE, VIX 9-11) ---
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
 # --- January 2026 (MIXED: flat then -5% mid-Jan, VIX 9->14) ---
 ("2026-01-01",26173.30,26146.55,59674.80,59711.55,9.19),("2026-01-02",26155.10,26328.55,59757.40,60150.95,9.45),
 ("2026-01-05",26333.70,26250.30,60360.40,60044.20,10.02),("2026-01-06",26189.70,26178.70,59957.80,60118.40,10.02),
 ("2026-01-07",26143.10,26140.75,60039.70,59990.85,9.95),("2026-01-08",26106.50,25876.85,59893.15,59686.50,10.60),
 ("2026-01-09",25840.40,25683.30,59558.15,59251.55,10.93),("2026-01-12",25669.05,25790.25,59217.25,59450.50,11.37),
 ("2026-01-13",25897.35,25732.30,59767.55,59578.80,11.20),("2026-01-14",25648.55,25665.60,59330.35,59580.15,11.32),
 ("2026-01-16",25696.05,25694.35,59590.35,60095.15,11.37),("2026-01-19",25653.10,25585.50,60093.30,59891.35,11.83),
 ("2026-01-20",25580.30,25232.50,59851.40,59404.20,12.73),("2026-01-21",25141.00,25157.50,59142.00,58800.30,13.78),
 ("2026-01-22",25344.15,25289.90,59194.25,59200.10,13.35),("2026-01-23",25344.60,25048.65,59305.15,58473.10,14.19),
 ("2026-01-27",25063.35,25175.40,58366.05,59205.45,14.45),("2026-01-28",25258.85,25342.75,59575.65,59598.80,13.53),
 ("2026-01-29",25345.00,25418.90,59416.25,59957.85,13.37),("2026-01-30",25247.55,25320.65,59542.25,59610.45,13.63),
 # --- March 2026 (CRASH, VIX 17-28) ---
 ("2026-03-02",24659.25,24865.70,59204.30,59839.65,17.13),("2026-03-04",24388.80,24480.50,58447.15,58755.25,21.14),
 ("2026-03-05",24615.95,24765.90,59008.25,59055.85,17.86),("2026-03-06",24656.40,24450.45,58629.60,57783.25,19.88),
 ("2026-03-09",23868.05,24028.05,56121.40,56019.80,23.36),("2026-03-10",24280.80,24261.60,56583.85,56950.80,18.91),
 ("2026-03-11",24231.85,23866.85,56790.40,55735.75,21.06),("2026-03-12",23674.85,23639.15,55008.20,55100.95,21.52),
 ("2026-03-13",23462.50,23151.10,54592.05,53757.85,22.65),("2026-03-16",23116.10,23408.80,53721.50,54413.40,21.60),
 ("2026-03-17",23493.20,23581.15,54649.10,54876.00,19.79),("2026-03-18",23632.90,23777.80,54927.05,55326.05,18.72),
 ("2026-03-19",23197.75,23002.15,53474.55,53451.00,22.80),("2026-03-20",23110.15,23114.50,53548.20,53427.05,22.81),
 ("2026-03-23",22824.35,22512.65,52576.10,51437.75,26.73),("2026-03-24",22878.45,22912.40,52384.80,52605.65,24.74),
 ("2026-03-25",23064.40,23306.45,53024.75,53708.10,24.64),("2026-03-27",23173.55,22819.60,53244.25,52274.60,26.80),
 ("2026-03-30",22549.65,22331.40,51527.90,50275.35,27.89),
 # --- April 2026 (downtrend->recovery, VIX 17-25) ---
 ("2026-04-01",22899.00,22679.40,51433.90,51448.65,25.01),("2026-04-02",22383.40,22713.10,50625.65,51548.75,25.52),
 ("2026-04-06",22780.30,22968.25,51747.60,52609.10,25.47),("2026-04-07",22838.70,23123.65,52258.70,52716.25,24.70),
 ("2026-04-08",23855.15,23997.35,54904.45,55703.90,19.70),("2026-04-09",23909.05,23775.10,55505.95,54821.70,20.43),
 ("2026-04-10",23880.55,24050.60,55182.25,55912.75,18.85),("2026-04-13",23589.60,23842.65,54646.00,55605.05,20.50),
 ("2026-04-15",24163.80,24231.30,56343.45,56301.95,18.67),("2026-04-16",24385.20,24196.75,56657.25,56086.40,18.09),
 ("2026-04-17",24165.90,24353.55,56072.40,56565.70,17.21),("2026-04-20",24391.50,24364.85,56704.05,56582.35,18.79),
 ("2026-04-21",24374.55,24576.60,56823.60,57371.45,17.53),("2026-04-22",24470.85,24378.10,57163.35,57124.45,18.30),
 ("2026-04-23",24202.35,24173.05,56608.95,56305.00,18.59),("2026-04-24",24100.55,23897.95,56170.20,56089.75,19.71),
 ("2026-04-27",23945.45,24092.70,56162.60,56264.30,18.38),("2026-04-28",24049.90,23995.70,55862.50,55400.35,18.05),
 ("2026-04-29",24096.90,24177.65,55634.50,55403.60,17.44),("2026-04-30",23996.95,23997.55,54880.65,54863.35,18.46),
 # --- May 2026 (chop->down, VIX 16-19) ---
 ("2026-05-04",24063.55,24119.30,54937.90,54878.50,18.30),("2026-05-05",24052.60,24032.80,54691.30,54547.05,17.91),
 ("2026-05-06",24171.00,24330.95,55113.40,55981.05,16.68),("2026-05-07",24398.50,24326.65,56114.00,56047.40,16.62),
 ("2026-05-08",24233.65,24176.15,55783.95,55310.55,16.84),("2026-05-11",23970.10,23815.85,54832.45,54439.90,18.55),
 ("2026-05-12",23722.60,23379.55,54178.40,53555.20,19.28),("2026-05-13",23362.45,23412.60,53600.40,53456.15,19.43),
 ("2026-05-14",23530.25,23689.60,53639.50,54128.95,18.61),("2026-05-15",23731.40,23643.50,54207.75,53710.35,18.79),
 ("2026-05-18",23482.20,23649.95,53282.15,53537.00,19.63),("2026-05-19",23675.30,23618.00,53553.75,53409.15,18.68),
]

SHORT_DELTA=0.20; WING_DELTA=0.10; PROFIT_TAKE=0.55; STOP_MULT=2.2
RISK=0.02*500000; LOT={"NIFTY":25,"BANKNIFTY":15}; MAXLOTS={"NIFTY":4,"BANKNIFTY":3}
# --- regime gate ---
VIX_MAX=14.0      # sell only when VIX at/below this (calm)
TREND_MAX=3.0     # skip if |NIFTY % move over LOOKBACK days| exceeds this (trending)
LOOKBACK=5
MIN_DTE=2         # skip degenerate near-expiry entries

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
rows.sort(key=lambda r:r["d"])
idx={r["d"]:i for i,r in enumerate(rows)}

def recent_trend_pct(entry_i):
    j=max(0,entry_i-LOOKBACK)
    a=rows[j]["NIFTY"][1]; b=rows[entry_i]["NIFTY"][0]
    return (b-a)/a*100.0 if a else 0.0

def regime_ok(entry_i):
    vix=rows[entry_i]["vix"]; tr=abs(recent_trend_pct(entry_i))
    return (vix<=VIX_MAX and tr<=TREND_MAX), vix, tr

def run_cycle(sym, entry_i, ed):
    spot0=rows[entry_i][sym][0]; iv0=ann_iv(rows[entry_i]["vix"])
    dte0=max(1.0,(ed-rows[entry_i]["d"]).days+0.3)
    sp=pick(spot0,dte0,iv0,"PE",SHORT_DELTA,-1); lp=pick(spot0,dte0,iv0,"PE",WING_DELTA,-1)
    sc=pick(spot0,dte0,iv0,"CE",SHORT_DELTA,1);  lc=pick(spot0,dte0,iv0,"CE",WING_DELTA,1)
    st=(sp,lp,sc,lc); credit=cval(spot0,dte0,iv0,st)
    if credit<=1: return None
    maxloss=max(sp-lp,lc-sc)-credit
    if maxloss<=0: return None
    lots=max(1,min(int(RISK/(maxloss*LOT[sym])),MAXLOTS[sym])); qty=lots*LOT[sym]
    cost=round(0.02*credit*qty+8*lots,2)
    ei=idx[ed]; exitv=None
    for j in range(entry_i+1,ei+1):
        spot=rows[j][sym][1]; iv_t=ann_iv(rows[j]["vix"])
        dte_t=max(0.02,(ed-rows[j]["d"]).days+(0.3 if j!=ei else 0.0))
        if j==ei: exitv=min(cval(spot,0.0,iv_t,st),maxloss+credit); break
        val=cval(spot,dte_t,iv_t,st)
        if val<=(1-PROFIT_TAKE)*credit: exitv=val; break
        if val>=STOP_MULT*credit: exitv=min(val,maxloss+credit); break
    if exitv is None: return None
    return (credit-exitv)*qty-cost

# generate cycles per (year,month) so they never span the Feb data gap
mk={}
for r in rows: mk.setdefault((r["d"].year,r["d"].month),[]).append(r["d"])
ungated={}; gated={}; skipped={}
for (y,m),mds in sorted(mk.items()):
    mds.sort()
    nifty_exp=[d for d in mds if d.weekday()==1]
    last=_cal.monthrange(y,m)[1]; bn_e=None
    for day in range(last,last-7,-1):
        if date(y,m,day).weekday()==1:
            c=[d for d in mds if d<=date(y,m,day)]; bn_e=max(c) if c else None; break
    for sym,exps in (("NIFTY",nifty_exp),("BANKNIFTY",[bn_e] if bn_e else [])):
        prev=None
        for ed in exps:
            cand=[d for d in mds if (prev is None or d>prev) and d<ed]
            prev=ed
            if not cand: continue
            entry=cand[0]; entry_i=idx[entry]
            if (ed-entry).days<MIN_DTE: continue   # degenerate
            pnl=run_cycle(sym,entry_i,ed)
            if pnl is None: continue
            ungated[(y,m)]=ungated.get((y,m),0.0)+pnl
            ok,vix,tr=regime_ok(entry_i)
            if ok: gated[(y,m)]=gated.get((y,m),0.0)+pnl
            else:  skipped[(y,m)]=skipped.get((y,m),0.0)+1

NAMES={(2025,12):"Dec'25 RANGE",(2026,1):"Jan'26 MIXED",(2026,3):"Mar'26 CRASH",(2026,4):"Apr'26 DOWN",(2026,5):"May'26 CHOP"}
print("\n==== REGIME-GATED CONDOR vs UNGATED — real data, 5 months ====")
print(f"gate: VIX<={VIX_MAX}, |{LOOKBACK}d trend|<={TREND_MAX}%, skip <{MIN_DTE}DTE")
print(f"{'month':16} {'avgVIX':>6} | {'UNGATED':>9} | {'GATED':>9} | skipped")
ug_t=g_t=0
for k in sorted(NAMES):
    mds=mk.get(k,[]); avgvix=sum(r['vix'] for r in rows if (r['d'].year,r['d'].month)==k)/max(1,len(mds))
    ug=ungated.get(k,0.0); g=gated.get(k,0.0); ug_t+=ug; g_t+=g
    print(f"{NAMES[k]:16} {avgvix:>6.1f} | {ug:>9.0f} | {g:>9.0f} | {int(skipped.get(k,0))}")
print(f"{'TOTAL':16} {'':>6} | {ug_t:>9.0f} | {g_t:>9.0f} |")
