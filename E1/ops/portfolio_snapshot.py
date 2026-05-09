import os
import dotenv
from alpaca.trading.client import TradingClient
from datetime import date

dotenv.load_dotenv()

def get_snapshot():
    client = TradingClient(
        api_key=os.getenv('E1_ALPACA_KEY'),
        secret_key=os.getenv('E1_ALPACA_SECRET'),
        paper=True
    )
    
    positions = client.get_all_positions()
    
    print("\n" + "="*70)
    print(f"STRATEGY E1: LIVE PORTFOLIO SNAPSHOT ({date.today()})")
    print("="*70)
    print(f"{'Ticker':<10} | {'Qty':<8} | {'Market Val':<15} | {'Unrealized P&L':<15}")
    print("-" * 70)
    
    total_val = 0.0
    total_pnl = 0.0
    
    for p in positions:
        mkt_val = float(p.market_value)
        pnl = float(p.unrealized_pl)
        total_val += mkt_val
        total_pnl += pnl
        
        print(f"{p.symbol:<10} | {p.qty:<8} | ${mkt_val:<14,.2f} | ${pnl:<14,.2f}")
        
    print("-" * 70)
    print(f"{'TOTAL':<10} | {'':<8} | ${total_val:<14,.2f} | ${total_pnl:<14,.2f}")
    print("="*70)

if __name__ == "__main__":
    get_snapshot()
