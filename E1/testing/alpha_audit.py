import duckdb
import pandas as pd
from datetime import date

conn = duckdb.connect(r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb')

results = []
years = range(2014, 2027)

for y in years:
    start_date = f"{y}-01-01"
    if y == 2026:
        end_date = "2026-05-01"
    else:
        end_date = f"{y}-12-31"
        
    # Get Strategy CAGR from manifest
    run_id = f"ANNUAL_AUDIT_{y}"
    c = conn.execute("SELECT cagr FROM sandbox.e1_sim_run_manifest WHERE sim_run_id = ?", [run_id]).fetchone()
    strat_cagr = c[0] if c else 0
    
    # Get SPY CAGR
    spy_start = conn.execute("SELECT close FROM refined.price_history WHERE ticker='SPY' AND date >= ? ORDER BY date LIMIT 1", [start_date]).fetchone()
    spy_end = conn.execute("SELECT close FROM refined.price_history WHERE ticker='SPY' AND date <= ? ORDER BY date DESC LIMIT 1", [end_date]).fetchone()
    
    # Get DIA CAGR
    dia_start = conn.execute("SELECT close FROM refined.price_history WHERE ticker='DIA' AND date >= ? ORDER BY date LIMIT 1", [start_date]).fetchone()
    dia_end = conn.execute("SELECT close FROM refined.price_history WHERE ticker='DIA' AND date <= ? ORDER BY date DESC LIMIT 1", [end_date]).fetchone()
    
    if spy_start and spy_end and dia_start and dia_end:
        days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days
        if days <= 0: days = 1
        
        spy_ret = (spy_end[0] / spy_start[0]) - 1
        spy_cagr = ((1 + spy_ret)**(365.0/days) - 1) * 100
        
        dia_ret = (dia_end[0] / dia_start[0]) - 1
        dia_cagr = ((1 + dia_ret)**(365.0/days) - 1) * 100
        
        results.append({
            'Year': y,
            'E1 CAGR': round(strat_cagr, 2),
            'SPY CAGR': round(spy_cagr, 2),
            'DIA CAGR': round(dia_cagr, 2),
            'Alpha vs SPY': round(strat_cagr - spy_cagr, 2)
        })

df = pd.DataFrame(results)
print("\n--- STRATEGY E1 ALPHA AUDIT (2014-2026) ---")
print(df.to_string(index=False))
