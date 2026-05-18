import duckdb

DB_PATH = r"C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb"
conn = duckdb.connect(DB_PATH, read_only=True)

print("Checking for duplicate rows in sandbox.e1_cte_lookup...")
res = conn.execute("""
    SELECT entry_regime, vix_momentum_bucket, regime_age_bucket, COUNT(*) 
    FROM sandbox.e1_cte_lookup 
    GROUP BY entry_regime, vix_momentum_bucket, regime_age_bucket 
    HAVING COUNT(*) > 1
""").df()

if len(res) > 0:
    print("\nDUPLICATE KEY FOUND!")
    print(res)
else:
    print("\nNo duplicates found in the database. The database table is perfectly unique!")

print("\nFull dump of database table:")
dump = conn.execute("""
    SELECT entry_regime, vix_momentum_bucket, regime_age_bucket, cte_multiplier, trade_count, t_stat, evidence 
    FROM sandbox.e1_cte_lookup
    ORDER BY entry_regime, vix_momentum_bucket, regime_age_bucket
""").df()
print(dump.to_string(index=False))

conn.close()
