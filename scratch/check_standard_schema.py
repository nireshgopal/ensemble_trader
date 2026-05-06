import duckdb

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def check_schema():
    con = duckdb.connect(DB_PATH)
    try:
        tables = ['sandbox.e1_positions', 'sandbox.e1_trade_log']
        for table in tables:
            print(f"\n--- {table} ---")
            res = con.execute(f"PRAGMA table_info('{table}')").fetchall()
            for col in res:
                print(col)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    check_schema()
