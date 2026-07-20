import sys
import os
from sqlmodel import Session, select

# Load path
sys.path.insert(0, '/Users/nithish-prabhu/Downloads/intra-day')

from services.chartedge_core.database import engine, StockPositionRecord

def main():
    print("Cleaning old open position records for LAURUSLABS and NYKAA...")
    with Session(engine) as session:
        stmt = select(StockPositionRecord).where(
            StockPositionRecord.status == "OPEN",
            StockPositionRecord.entry_date != "2026-07-20"
        )
        old_records = session.exec(stmt).all()
        
        print(f"Found {len(old_records)} old open records to delete:")
        for rec in old_records:
            print(f"  Symbol: {rec.symbol}, ID: {rec.position_id}, Date: {rec.entry_date}")
            session.delete(rec)
            
        if old_records:
            session.commit()
            print("Successfully deleted old records.")
        else:
            print("No old records found.")

if __name__ == "__main__":
    main()
