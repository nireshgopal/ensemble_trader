import duckdb

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def audit_triggers():
    con = duckdb.connect(DB_PATH)
    try:
        query = """
            SELECT exit_trigger, COUNT(*) as count
            FROM sandbox.e1_sim_positions
            WHERE strftime('%Y', exit_date) = '2018'
            AND sim_run_id = '8e46c6c1-41c'
            GROUP BY exit_trigger
            ORDER BY count DESC
        """
        res = con.execute(query).fetchall()
        print(f"{'Exit Trigger':<50} {'Count':<6}")
        print("-" * 60)
        for row in res:
            print(f"{str(row[0]):<50} {row[1]:<6}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    audit_triggers()
