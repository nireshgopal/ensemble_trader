import duckdb

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def check_triggers():
    con = duckdb.connect(DB_PATH)
    try:
        res = con.execute("""
            SELECT exit_trigger, COUNT(*) 
            FROM sandbox.e1_sim_positions 
            WHERE sim_run_id = '8e46c6c1-41c' AND status = 'CLOSED' 
            GROUP BY 1
        """).fetchall()
        for row in res:
            print(row)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    check_triggers()
