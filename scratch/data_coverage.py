import sys
import os
sys.path.insert(0, os.getcwd())
import duckdb
from E1.core.config import DB_PATH

def review_data_coverage():
    con = duckdb.connect(DB_PATH)
    tables = [
        'refined.daily_signals_ml',
        'refined.ensemble_daily_scores',
        'refined.market_regime',
        'refined.price_history',
        'refined.macro_daily',
        'refined.historical_sector_rs',
        'refined.financials',
        'yahoo.earnings_calendar'
    ]
    
    print(f"{'Table':<35} | {'Min Date':<12} | {'Max Date':<12} | {'Count':<10}")
    print("-" * 75)
    for t in tables:
        try:
            date_col = 'date'
            if t == 'refined.historical_sector_rs':
                date_col = 'as_of_date'
            elif t == 'refined.financials':
                date_col = 'report_date'
            elif t == 'yahoo.earnings_calendar':
                date_col = 'next_earnings_date'
            
            res = con.execute(f"SELECT MIN({date_col}), MAX({date_col}), COUNT(*) FROM {t}").fetchone()
            print(f"{t:<35} | {str(res[0]):<12} | {str(res[1]):<12} | {res[2]:<10}")
        except Exception as e:
            print(f"{t:<35} | Error: {e}")
    
    con.close()

if __name__ == "__main__":
    review_data_coverage()
