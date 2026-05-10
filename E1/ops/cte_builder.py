import duckdb
import pandas as pd
import numpy as np
from datetime import datetime

# Path to database
DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'

def build_cte():
    conn = duckdb.connect(DB_PATH)
    
    print("[1/6] Extracting Training Set (Audit Gold)...")
    # 1. Get raw trades
    trades_query = """
    SELECT 
        sim_run_id,
        ticker,
        entry_date,
        entry_regime,
        pnl_dollars,
        exit_trigger
    FROM sandbox.e1_sim_positions
    WHERE sim_run_id LIKE 'ANNUAL_AUDIT_%'
      AND exit_trigger != 'ALPACA_SYNC_DESYNC'
    """
    df_trades = conn.execute(trades_query).df()
    df_trades['entry_date'] = pd.to_datetime(df_trades['entry_date'])
    
    # 2. Get VIX data for momentum
    print("[2/6] Calculating VIX Momentum...")
    vix_query = "SELECT date, close as vix_close FROM refined.price_history WHERE ticker = '$VIX' ORDER BY date"
    df_vix = conn.execute(vix_query).df()
    df_vix['date'] = pd.to_datetime(df_vix['date'])
    df_vix = df_vix.sort_values('date')
    df_vix['vix_20d_ago'] = df_vix['vix_close'].shift(20)
    df_vix['vix_momentum'] = (df_vix['vix_close'] - df_vix['vix_20d_ago']) / df_vix['vix_20d_ago']
    
    # 3. Get Regime data for age
    print("[3/6] Calculating Regime Age...")
    regime_query = "SELECT date, regime FROM refined.market_regime ORDER BY date"
    df_regime = conn.execute(regime_query).df()
    df_regime['date'] = pd.to_datetime(df_regime['date'])
    df_regime = df_regime.sort_values('date')
    
    # Calculate regime age (days since last change)
    df_regime['regime_change'] = df_regime['regime'] != df_regime['regime'].shift(1)
    df_regime['regime_group'] = df_regime['regime_change'].cumsum()
    df_regime['regime_age_days'] = df_regime.groupby('regime_group').cumcount()
    
    # 4. Merge features into trades
    df = df_trades.merge(df_vix[['date', 'vix_momentum']], left_on='entry_date', right_on='date', how='left')
    df = df.merge(df_regime[['date', 'regime_age_days']], left_on='entry_date', right_on='date', how='left')
    
    # Drop rows with missing features
    df = df.dropna(subset=['vix_momentum', 'regime_age_days'])
    
    # 5. Binning
    print("[4/6] Creating Dynamic Bins (Quintiles)...")
    # VIX Momentum Quintiles
    vix_q = df['vix_momentum'].quantile([0.2, 0.4, 0.6, 0.8]).values
    def get_vix_bin(v):
        if v < vix_q[0]: return 'VIX_COLLAPSING'
        if v < vix_q[1]: return 'VIX_FALLING'
        if v < vix_q[2]: return 'VIX_STABLE'
        if v < vix_q[3]: return 'VIX_RISING'
        return 'VIX_SPIKING'
    
    df['vix_momentum_bucket'] = df['vix_momentum'].apply(get_vix_bin)
    
    # Regime Age Bins (Per Spec)
    def get_age_bin(a):
        if a < 15: return 'REGIME_FRESH'
        if a <= 90: return 'REGIME_ESTABLISHED'
        return 'REGIME_MATURE'
    
    df['regime_age_bucket'] = df['regime_age_days'].apply(get_age_bin)
    
    # 6. Group and Calculate Stats
    print("[5/6] Calculating Shrunk Statistics (Bayesian k*)...")
    global_mean = df['pnl_dollars'].mean()
    
    # Calculate within-cell variance
    cell_groups = df.groupby(['entry_regime', 'vix_momentum_bucket', 'regime_age_bucket'])
    cell_stats = cell_groups['pnl_dollars'].agg(['count', 'mean', 'std', 'var']).reset_index()
    
    # Calculate episode counts (approximate using entry dates)
    # A real episode count would check how many independent regime blocks contributed to these trades.
    # For now, we'll use a simplified version: count unique sim_run_ids per cell.
    eps = cell_groups['sim_run_id'].nunique().reset_index(name='episode_count')
    cell_stats = cell_stats.merge(eps, on=['entry_regime', 'vix_momentum_bucket', 'regime_age_bucket'])
    
    # T2 Hit Rate
    t2_hits = cell_groups['exit_trigger'].apply(lambda x: (x.str.contains('Target 2')).sum() / len(x)).reset_index(name='t2_hit_rate')
    cell_stats = cell_stats.merge(t2_hits, on=['entry_regime', 'vix_momentum_bucket', 'regime_age_bucket'])
    
    # k* Calculation
    sigma2_within = cell_stats['var'].mean()
    sigma2_between = cell_stats['mean'].var()
    k_star = sigma2_within / sigma2_between if sigma2_between > 0 else 100
    print(f"   Optimum Shrinkage (k*): {k_star:.2f}")
    
    # Shrinkage Formula
    cell_stats['w'] = cell_stats['count'] / (cell_stats['count'] + k_star)
    cell_stats['shrunk_avg_pnl'] = cell_stats['w'] * cell_stats['mean'] + (1 - cell_stats['w']) * global_mean
    
    # Multiplier Mapping
    cell_stats['ratio'] = cell_stats['shrunk_avg_pnl'] / global_mean
    cell_stats['cte_multiplier'] = (1.0 + 0.10 * (cell_stats['ratio'] - 1.0)).clip(0.90, 1.10)
    
    # Data Quality Flag
    cell_stats['data_quality'] = np.where((cell_stats['count'] < 10) | (cell_stats['episode_count'] < 5), 'WEAK', 'MODERATE')
    
    # Apply floor for WEAK cells
    cell_stats.loc[cell_stats['data_quality'] == 'WEAK', 'cte_multiplier'] = 1.0
    
    # Final cleanup for DDL
    cell_stats['global_avg_pnl'] = global_mean
    cell_stats['last_updated'] = datetime.now()
    
    # Rename columns to match DDL
    final_df = cell_stats.rename(columns={
        'mean': 'raw_avg_pnl',
        'std': 'pnl_stddev',
        'count': 'trade_count'
    })
    
    cols = [
        'entry_regime', 'vix_momentum_bucket', 'regime_age_bucket',
        'trade_count', 'episode_count', 'raw_avg_pnl', 'shrunk_avg_pnl',
        'global_avg_pnl', 'pnl_stddev', 't2_hit_rate', 'data_quality',
        'cte_multiplier', 'last_updated'
    ]
    
    # 7. Write to DB
    print("[6/6] Writing to sandbox.e1_cte_lookup...")
    conn.execute("DROP TABLE IF EXISTS sandbox.e1_cte_lookup")
    conn.execute("CREATE TABLE sandbox.e1_cte_lookup AS SELECT * FROM final_df")
    
    print("\n✅ CTE BUILD COMPLETE.")
    print(f"Total Cells Created: {len(final_df)}")
    print(f"MODERATE Quality Cells: {len(final_df[final_df['data_quality'] == 'MODERATE'])}")
    print(df[['vix_momentum_bucket', 'vix_momentum']].groupby('vix_momentum_bucket').agg(['min', 'max']))

if __name__ == "__main__":
    build_cte()
