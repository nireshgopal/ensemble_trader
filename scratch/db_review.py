import duckdb
import pandas as pd

db_path = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def audit_db():
    try:
        con = duckdb.connect(db_path, read_only=True)
        
        # 1. List all tables across schemas
        tables = con.execute("""
            SELECT table_schema, table_name 
            FROM information_schema.tables 
            WHERE table_schema IN ('refined', 'yahoo', 'sandbox')
            ORDER BY table_schema, table_name
        """).df()
        
        print("--- Database Tables ---")
        print(tables)
        
        # 2. Focus on e1_sim tables in sandbox
        sim_tables = tables[tables['table_name'].str.contains('e1_sim')]
        print("\n--- Strategy E1 Simulation Tables ---")
        for _, row in sim_tables.iterrows():
            table_path = f"{row['table_schema']}.{row['table_name']}"
            count = con.execute(f"SELECT COUNT(*) FROM {table_path}").fetchone()[0]
            print(f"{table_path}: {count} rows")
            
            # Show schema for a few key ones
            if 'results' in row['table_name'] or 'positions' in row['table_name']:
                print(f"Schema for {table_path}:")
                print(con.execute(f"DESCRIBE {table_path}").df()[['column_name', 'column_type']])
        
        # 3. Check for the specific issue mentioned in handover: sim_run_id and sequence constraints
        # Also check e1_positions id integrity
        if 'e1_positions' in tables['table_name'].values:
            print("\n--- e1_positions ID Integrity Check ---")
            id_check = con.execute("SELECT id FROM sandbox.e1_positions LIMIT 5").df()
            print(id_check)
            
        con.close()
    except Exception as e:
        print(f"Error auditing database: {e}")

if __name__ == "__main__":
    audit_db()
