import duckdb

DB_PATH = r"C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb"
conn = duckdb.connect(DB_PATH, read_only=True)

print("Verifying what database lookup returns for HEALTHY / RISING / FRESH...")
res = conn.execute("""
    SELECT cte_multiplier, trade_count, t_stat, evidence FROM sandbox.e1_cte_lookup
    WHERE entry_regime = 'HEALTHY'
      AND vix_momentum_bucket = 'RISING'
      AND regime_age_bucket = 'FRESH'
    LIMIT 1;
""").df()

print(res)

print("\nVerifying row count for HEALTHY / RISING / FRESH:")
res_all = conn.execute("""
    SELECT entry_regime, vix_momentum_bucket, regime_age_bucket, cte_multiplier, evidence 
    FROM sandbox.e1_cte_lookup
    WHERE entry_regime = 'HEALTHY'
      AND vix_momentum_bucket = 'RISING'
      AND regime_age_bucket = 'FRESH';
""").df()
print(res_all)

conn.close()
