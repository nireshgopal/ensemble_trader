import duckdb
import pandas as pd
from E1.core import config

conn = duckdb.connect(config.DB_PATH)
df = conn.execute("SELECT ticker, entry_date, entry_price, shares, dollar_value FROM sandbox.e1_sim_positions").df()
print(df)
conn.close()
