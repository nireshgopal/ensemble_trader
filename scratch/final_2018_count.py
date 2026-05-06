import duckdb

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def final_count():
    con = duckdb.connect(DB_PATH)
    try:
        # EXACT MATCH (User's SQL)
        res_exact = con.execute("""
            SELECT COUNT(*) as total,
                   COUNT(DISTINCT ticker) as unique_tickers
            FROM sandbox.e1_sim_positions
            WHERE exit_trigger = 'SCORE_DECAY_VETO'
            AND strftime('%Y', exit_date) = '2018'
            AND sim_run_id = '8e46c6c1-41c'
        """).fetchone()
        
        # PARTIAL MATCH (Capturing variants)
        res_like = con.execute("""
            SELECT COUNT(*) as total,
                   COUNT(DISTINCT ticker) as unique_tickers
            FROM sandbox.e1_sim_positions
            WHERE exit_trigger LIKE 'SCORE_DECAY_VETO%'
            AND strftime('%Y', exit_date) = '2018'
            AND sim_run_id = '8e46c6c1-41c'
        """).fetchone()
        
        print(f"EXACT MATCH (= 'SCORE_DECAY_VETO'):")
        print(f"  Total: {res_exact[0]}, Unique Tickers: {res_exact[1]}")
        print(f"\nPARTIAL MATCH (LIKE 'SCORE_DECAY_VETO%'):")
        print(f"  Total: {res_like[0]}, Unique Tickers: {res_like[1]}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    final_count()
