import duckdb
import pandas as pd

conn = duckdb.connect(r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb')

query = """
SELECT 
    entry_regime, 
    COUNT(*) as trades 
FROM sandbox.e1_sim_positions 
WHERE sim_run_id LIKE 'ANNUAL_AUDIT_%' 
  AND exit_trigger != 'ALPACA_SYNC_DESYNC' 
GROUP BY entry_regime
"""

df = conn.execute(query).df()
print("\n--- GOLD SET: REGIME TRADE COUNTS ---")
print(df.to_string(index=False))
