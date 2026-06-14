from services.chartedge_core.database import clear_all_trades

if __name__ == "__main__":
    print("Clearing all trades from database...")
    clear_all_trades()
    print("Database cleared successfully.")
