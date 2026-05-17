import duckdb

db_path = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'
con = duckdb.connect(db_path, read_only=True)

schemas = ['refined', 'yahoo', 'sandbox']
for schema in schemas:
    print(f"--- Schema: {schema} ---")
    try:
        tables = con.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'").df()
        for idx, row in tables.iterrows():
            table = row['table_name']
            print(f"Table: {table}")
            columns = con.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = '{schema}' AND table_name = '{table}'").df()
            print(columns.to_string(index=False))
            print()
    except Exception as e:
        print(f"Error querying schema {schema}: {e}")
    print("\n")
con.close()
