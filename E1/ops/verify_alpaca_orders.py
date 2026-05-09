import os
import dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from datetime import date

dotenv.load_dotenv()

def verify_alpaca_history():
    client = TradingClient(
        api_key=os.getenv('E1_ALPACA_KEY'),
        secret_key=os.getenv('E1_ALPACA_SECRET'),
        paper=True
    )
    
    orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL))
    
    print("\n" + "="*60)
    print(f"ALPACA ORDER HISTORY: {date.today()}")
    print("="*60)
    
    today = date.today().isoformat()
    found = False
    
    for o in orders:
        created_date = o.created_at.date().isoformat()
        if created_date == today:
            print(f"{o.symbol:<10} | {o.status:<10} | {o.side:<6} | Qty: {o.qty:<8} | ID: {o.id}")
            found = True
            
    if not found:
        print("No orders found for today.")
    print("="*60)

if __name__ == "__main__":
    verify_alpaca_history()
