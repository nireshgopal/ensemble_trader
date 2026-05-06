import duckdb

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def check_dec_decay():
    con = duckdb.connect(DB_PATH)
    try:
        query = """
            SELECT 
                ticker,
                exit_date,
                pnl_pct,
                dollar_value,
                (stop_loss - entry_price) / entry_price * 100 as max_loss_pct_if_stopped
            FROM sandbox.e1_sim_positions
            WHERE exit_trigger LIKE 'SCORE_DECAY_VETO%'
            AND strftime('%Y-%m', exit_date) = '2018-12'
            ORDER BY pnl_pct ASC
        """
        res = con.execute(query).fetchall()
        if not res:
            print("No Score Decay exits found in December 2018.")
            return
            
        print(f"{'Ticker':<8} {'Exit Date':<12} {'PnL%':<10} {'Value':<10} {'MaxLoss%':<10}")
        print("-" * 60)
        for row in res:
            print(f"{row[0]:<8} {str(row[1]):<12} {row[2]*100:>8.2f}% ${row[3]:>8.2f} {row[4]:>8.2f}%")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    check_dec_decay()
