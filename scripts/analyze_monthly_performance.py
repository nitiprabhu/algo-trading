#!/usr/bin/env python3
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session
from sqlalchemy import select
from services.chartedge_core.database import get_engine, TradeRecord, StockPositionRecord

def analyze_performance():
    engine = get_engine()
    
    # Let's look at the last 30 days
    thirty_days_ago = datetime.now() - timedelta(days=30)
    cutoff_date_str = thirty_days_ago.strftime("%Y-%m-%d")
    
    print("="*60)
    print(f"📊 TRADING PERFORMANCE SUMMARY (Since {cutoff_date_str})")
    print("="*60)
    
    with Session(engine) as session:
        # 1. Options Trading (Mock or Real)
        stmt_options = select(TradeRecord).where(
            (TradeRecord.status == "CLOSED") & 
            (TradeRecord.exit_time >= cutoff_date_str)
        ).order_by(TradeRecord.exit_time)
        
        options_trades = session.execute(stmt_options).scalars().all()
        
        opts_wins = 0
        opts_losses = 0
        opts_pnl = 0.0
        opts_details = []
        
        for t in options_trades:
            # We filter only positional options if needed, but here we summarize all closed options/futures.
            pnl = float(t.pnl) if t.pnl else 0.0
            if pnl > 0:
                opts_wins += 1
            elif pnl < 0:
                opts_losses += 1
                
            opts_pnl += pnl
            opts_details.append(f"{t.exit_time} | {t.instrument} | {'Win 🟢' if pnl>0 else 'Loss 🔴'} | ₹{pnl:.2f}")

        print("\n📈 OPTIONS / FUTURES TRADING (Mock/Live):")
        print(f"Total Trades: {len(options_trades)}")
        print(f"Wins: {opts_wins} | Losses: {opts_losses}")
        print(f"Win Rate: {(opts_wins/len(options_trades)*100):.1f}%" if len(options_trades) > 0 else "Win Rate: 0%")
        print(f"Net PnL: ₹{opts_pnl:.2f}")
        
        if options_trades:
            print("\nRecent Trades:")
            for d in opts_details[-5:]: # show last 5
                print("  " + d)
                
        # 2. Positional Stocks (Real Investing)
        stmt_stocks = select(StockPositionRecord).where(
            (StockPositionRecord.status == "CLOSED") &
            (StockPositionRecord.exit_date >= cutoff_date_str)
        ).order_by(StockPositionRecord.exit_date)
        
        stock_trades = session.execute(stmt_stocks).scalars().all()
        
        stocks_wins = 0
        stocks_losses = 0
        stocks_pnl = 0.0
        stock_details = []
        
        for t in stock_trades:
            pnl = float(t.pnl) if t.pnl else 0.0
            if pnl > 0:
                stocks_wins += 1
            elif pnl < 0:
                stocks_losses += 1
                
            stocks_pnl += pnl
            stock_details.append(f"{t.exit_date} | {t.symbol} | {'Win 🟢' if pnl>0 else 'Loss 🔴'} | ₹{pnl:.2f}")

        print("\n📈 POSITIONAL STOCKS (Real Investing):")
        print(f"Total Trades Closed: {len(stock_trades)}")
        print(f"Wins: {stocks_wins} | Losses: {stocks_losses}")
        print(f"Win Rate: {(stocks_wins/len(stock_trades)*100):.1f}%" if len(stock_trades) > 0 else "Win Rate: 0%")
        print(f"Net PnL: ₹{stocks_pnl:.2f}")
        
        if stock_trades:
            print("\nRecent Trades:")
            for d in stock_details[-5:]:
                print("  " + d)

if __name__ == "__main__":
    analyze_performance()
