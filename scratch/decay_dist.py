import duckdb

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def check_decay_dist():
    con = duckdb.connect(DB_PATH)
    try:
        query = """
            SELECT 
                strftime('%Y-%m', exit_date) as month,
                exit_trigger,
                COUNT(*) as count,
                AVG(pnl_pct) as avg_pnl
            FROM sandbox.e1_sim_positions
            WHERE exit_trigger LIKE 'SCORE_DECAY_VETO%' AND sim_run_id = '8e46c6c1-41c'
            GROUP BY 1
            ORDER BY 1
        """
        res = con.execute(query).fetchall()
        print(f"{'Month':<10} {'Count':<6} {'Avg PnL%':<10}")
        print("-" * 30)
        for row in res:
            print(f"{row[0]:<10} {row[2]:<6} {row[3]*100:>8.2f}%")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    check_decay_dist()
