import duckdb
import pandas as pd
from datetime import datetime

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def test_cte(test_date, regime):
    conn = duckdb.connect(DB_PATH)
    
    # 1. Calculate features for the test date
    # VIX Momentum
    vix_q = "SELECT date, close as vix_close FROM refined.price_history WHERE ticker = '$VIX' AND date <= ? ORDER BY date DESC LIMIT 21"
    df_vix = conn.execute(vix_q, [test_date]).df()
    vix_now = df_vix.iloc[0]['vix_close']
    vix_20d = df_vix.iloc[-1]['vix_close']
    vix_mom = (vix_now - vix_20d) / vix_20d
    
    # VIX Binning (We need the quintiles from the training set to be consistent)
    # For this test, we'll just pull them from the builder logic or hardcode for demo
    # In production, the cte_engine will have these cached.
    # Let's get the quintiles from the full history
    all_vix_q = conn.execute("""
        WITH mom AS (
            SELECT date, close, LAG(close, 20) OVER (ORDER BY date) as prev
            FROM refined.price_history WHERE ticker = '$VIX'
        )
        SELECT 
            quantile_cont((close - prev)/prev, 0.2) as q20,
            quantile_cont((close - prev)/prev, 0.4) as q40,
            quantile_cont((close - prev)/prev, 0.6) as q60,
            quantile_cont((close - prev)/prev, 0.8) as q80
        FROM mom
    """).df()
    vq = all_vix_q.iloc[0].values
    
    def get_vix_bin(v):
        if v < vq[0]: return 'VIX_COLLAPSING'
        if v < vq[1]: return 'VIX_FALLING'
        if v < vq[2]: return 'VIX_STABLE'
        if v < vq[3]: return 'VIX_RISING'
        return 'VIX_SPIKING'
    
    vix_bin = get_vix_bin(vix_mom)
    
    # Regime Age
    # Find the last regime change date
    age_q = """
    WITH changes AS (
        SELECT date, regime, LAG(regime) OVER (ORDER BY date) as prev_regime
        FROM refined.market_regime
        WHERE date <= ?
    )
    SELECT MAX(date) as last_change
    FROM changes
    WHERE regime != prev_regime OR prev_regime IS NULL
    """
    last_change = conn.execute(age_q, [test_date]).fetchone()[0]
    regime_age = (pd.to_datetime(test_date) - pd.to_datetime(last_change)).days
    
    def get_age_bin(a):
        if a < 15: return 'REGIME_FRESH'
        if a <= 90: return 'REGIME_ESTABLISHED'
        return 'REGIME_MATURE'
    
    age_bin = get_age_bin(regime_age)
    
    # 2. Look up in CTE table
    lookup_q = "SELECT * FROM sandbox.e1_cte_lookup WHERE entry_regime = ? AND vix_momentum_bucket = ? AND regime_age_bucket = ?"
    res = conn.execute(lookup_q, [regime, vix_bin, age_bin]).df()
    
    print(f"\n--- CTE VALIDATION: {test_date} ---")
    print(f"Input Regime: {regime}")
    print(f"VIX Momentum: {vix_mom:.2%} -> {vix_bin}")
    print(f"Regime Age:   {regime_age} Days -> {age_bin}")
    print("-" * 30)
    
    if len(res) > 0:
        row = res.iloc[0]
        print(f"CTE Multiplier: {row['cte_multiplier']:.2f}x")
        print(f"Data Quality:   {row['data_quality']}")
        print(f"Sample Size:    {row['trade_count']} trades ({row['episode_count']} episodes)")
        print(f"Raw Avg PnL:    ${row['raw_avg_pnl']:.2f}")
        print(f"Shrunk Avg PnL: ${row['shrunk_avg_pnl']:.2f}")
    else:
        print("RESULT: No matching bucket found in training set (1.00x fallback)")

if __name__ == "__main__":
    # Test 1: A "Recovery" day (e.g., late April 2020)
    test_cte('2020-04-20', 'HEALTHY')
    
    # Test 2: A "Late Cycle Trap" day (e.g., late 2021)
    test_cte('2021-11-15', 'HEALTHY')
    
    # Test 3: A "Panic" day (e.g., June 2022)
    test_cte('2022-06-13', 'FRAGILE')
