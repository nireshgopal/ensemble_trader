import duckdb
import os
import sys
sys.path.insert(0, os.getcwd())
from E1.core import config

conn = duckdb.connect(config.DB_PATH)
res = conn.execute("""
    SELECT date, COUNT(*) 
    FROM refined.ensemble_daily_scores 
    WHERE date BETWEEN '2017-01-01' AND '2017-12-31' 
    GROUP BY 1 
    ORDER BY 2 DESC 
    LIMIT 5
""").fetchall()
print(res)
conn.close()
