import duckdb

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def check_june_decay():
    con = duckdb.connect(DB_PATH)
    try:
        query = """
            SELECT ticker, exit_date, exit_price, pnl_pct, dollar_value
            FROM sandbox.e1_sim_positions
            WHERE exit_trigger LIKE 'SCORE_DECAY_VETO%'
            AND strftime('%Y-%m', exit_date) = '2018-06'
            AND sim_run_id = '8e46c6c1-41c'
        """
        res = con.execute(query).fetchall()
        if not res:
            print("No Score Decay exits found in June 2018.")
            return
            
        print(f"{'Ticker':<8} {'Exit Date':<12} {'Exit Price':<12} {'PnL%':<10} {'Value':<10}")
        print("-" * 60)
        for row in res:
            print(f"{row[0]:<8} {str(row[1]):<12} ${row[2]:>10.2f} {row[3]*100:>8.2f}% ${row[4]:>8.2f}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    check_june_decay()
