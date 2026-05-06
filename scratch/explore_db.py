import duckdb
import pandas as pd

DB_PATH = r"C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb"

def explore():
    conn = duckdb.connect(DB_PATH, read_only=True)
    
    print("=== SCHEMAS ===")
    schemas = conn.execute("SELECT DISTINCT table_schema FROM information_schema.tables").df()
    print(schemas)
    
    for schema in ['refined', 'sandbox', 'yahoo', 'alpaca', 'schwab']:
        print(f"\n=== TABLES IN {schema.upper()} ===")
        tables = conn.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'").df()
        print(tables.to_string(index=False))

    print("\n=== SAMPLE: refined.market_regime (Latest 5) ===")
    try:
        regime = conn.execute("SELECT * FROM refined.market_regime ORDER BY date DESC LIMIT 5").df()
        print(regime)
    except Exception as e:
        print(f"Error reading market_regime: {e}")

    print("\n=== SAMPLE: sandbox.e1_positions (Status summary) ===")
    try:
        positions = conn.execute("SELECT status, count(*) FROM sandbox.e1_positions GROUP BY status").df()
        print(positions)
    except Exception as e:
        print(f"Error reading e1_positions: {e}")

if __name__ == "__main__":
    explore()
