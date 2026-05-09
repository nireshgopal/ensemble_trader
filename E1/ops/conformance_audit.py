import os
import dotenv
import duckdb
from alpaca.trading.client import TradingClient

dotenv.load_dotenv()

def check_conformance():
    client = TradingClient(
        api_key=os.getenv('E1_ALPACA_KEY'),
        secret_key=os.getenv('E1_ALPACA_SECRET'),
        paper=True
    )
    
    # 1. Get Alpaca Positions
    alpaca_pos = {p.symbol: float(p.qty) for p in client.get_all_positions()}
    
    # 2. Get Database Positions
    DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'
    conn = duckdb.connect(DB_PATH)
    db_rows = conn.execute("SELECT ticker, shares FROM sandbox.e1_positions").fetchall()
    db_pos = {r[0]: float(r[1]) for r in db_rows}
    
    print("\n" + "="*50)
    print("STRATEGY E1: ALPACA vs DB CONFORMANCE REPORT")
    print("="*50)
    
    all_tickers = set(alpaca_pos.keys()) | set(db_pos.keys())
    
    mismatches = []
    print(f"{'Ticker':<10} | {'Alpaca Qty':<12} | {'DB Qty':<12} | {'Status'}")
    print("-" * 50)
    
    for t in sorted(all_tickers):
        a_qty = alpaca_pos.get(t, 0.0)
        d_qty = db_pos.get(t, 0.0)
        
        status = "MATCH" if a_qty == d_qty else "MISMATCH"
        if a_qty != d_qty:
            mismatches.append(t)
            
        print(f"{t:<10} | {a_qty:<12} | {d_qty:<12} | {status}")
        
    print("\n" + "="*50)
    if not mismatches:
        print("RESULT: FULL CONFORMANCE. All systems in sync.")
    else:
        print(f"RESULT: {len(mismatches)} MISMATCHES FOUND!")
        print(f"Affected Tickers: {', '.join(mismatches)}")
    print("="*50)

if __name__ == "__main__":
    check_conformance()
