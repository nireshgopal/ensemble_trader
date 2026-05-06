import duckdb

db_path = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'
con = duckdb.connect(db_path)

print("Tables in refined schema:")
print(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'refined'").df())

print("\nTables in sandbox schema:")
print(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'sandbox'").df())

print("\nTables in yahoo schema:")
print(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'yahoo'").df())

con.close()
