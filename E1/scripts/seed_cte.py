import duckdb
import pandas as pd
import numpy as np
from datetime import datetime
import os

# Database Connection
DB_PATH = r"C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb"
conn = duckdb.connect(DB_PATH, read_only=False)

print("Starting E1 CTE Lookup Table Seeding...")

# 1. Fetch market regimes & transition dates
print("Analyzing market regime history for age bucket calculation...")
regime_df = conn.execute("""
    WITH regime_changes AS (
        SELECT 
            date, 
            regime,
            LAG(regime) OVER (ORDER BY date) AS prev_regime
        FROM refined.market_regime
    )
    SELECT date, regime
    FROM regime_changes
    WHERE prev_regime IS NULL OR regime != prev_regime
    ORDER BY date
""").df()

regime_df['date'] = pd.to_datetime(regime_df['date'])

def get_regime_age_days(date_str, regime):
    if pd.isna(date_str) or pd.isna(regime):
        return 0
    t_date = pd.to_datetime(date_str)
    past_transitions = regime_df[regime_df['date'] <= t_date]
    if past_transitions.empty:
        return 0
    last_transition = past_transitions.iloc[-1]['date']
    return (t_date - last_transition).days

def get_age_bucket(age_days):
    if age_days < 30:
        return 'FRESH'
    elif age_days < 90:
        return 'ESTABLISHED'
    else:
        return 'MATURE'

# Hardcoded VIX thresholds calculated from 12-year history
p33 = -1.56
p67 = 1.21

def get_vix_bin(vel):
    if pd.isna(vel):
        return 'STABLE'
    if vel <= p33:
        return 'FALLING'
    elif vel <= p67:
        return 'STABLE'
    else:
        return 'RISING'

# 2. Fetch all Forensic Audit simulation positions
print("Fetching closed forensic simulation positions...")
FORENSIC_RUNS = "('phase5_seg1', 'phase5_seg2', 'phase5_seg3', 'phase5_2025_oos')"

positions_df = conn.execute(f"""
    SELECT
        p.ticker,
        p.entry_date,
        p.pnl_dollars,
        p.pnl_pct,
        p.initial_stop,
        p.stop_loss,
        p.shares,
        p.entry_price,
        p.exit_price,
        p.exit_trigger,
        p.atr_at_entry,
        COALESCE(p.regime_at_entry, mr.regime) AS entry_regime_resolved,
        COALESCE(NULLIF(ABS(p.entry_price - p.initial_stop), 0) * p.shares, NULLIF(ABS(p.entry_price - p.stop_loss), 0) * p.shares, 2.5 * COALESCE(p.atr_at_entry, 1.0) * p.shares) AS risk_dollars
    FROM sandbox.e1_sim_positions p
    LEFT JOIN refined.market_regime mr ON mr.date = CAST(p.entry_date AS DATE)
    WHERE p.sim_run_id IN {FORENSIC_RUNS}
      AND p.status = 'CLOSED'
      AND p.exit_trigger NOT IN ('ALPACA_SYNC_DESYNC', 'ALMANAC_EXIT_VETO', 'TIME_EXIT_EXT')
""").df()

# Merge with VIX history
print("Merging VIX velocity at entry dates...")
vix_df = conn.execute("""
    SELECT date, vix_close,
           vix_close - LAG(vix_close, 20) OVER (ORDER BY date) AS vix_velocity
    FROM refined.market_regime
    ORDER BY date
""").df()

positions_df['entry_date_ts'] = pd.to_datetime(positions_df['entry_date'])
vix_df['date_ts'] = pd.to_datetime(vix_df['date'])

merged_df = pd.merge_asof(
    positions_df.sort_values('entry_date_ts'),
    vix_df[['date_ts', 'vix_velocity']].sort_values('date_ts'),
    left_on='entry_date_ts',
    right_on='date_ts',
    direction='backward'
)

merged_df['vix_momentum_bucket'] = merged_df['vix_velocity'].apply(get_vix_bin)
merged_df['regime_age'] = merged_df.apply(lambda r: get_regime_age_days(r['entry_date'], r['entry_regime_resolved']), axis=1)
merged_df['regime_age_bucket'] = merged_df['regime_age'].apply(get_age_bucket)
merged_df['return_per_risk'] = merged_df['pnl_dollars'] / merged_df['risk_dollars']

# 3. Build the seeding matrix dataframe with all 27 combinations
print("Calculating stats and multipliers for all 27 context cells...")
seeding_records = []
all_possible_regimes = ['HEALTHY', 'FRAGILE', 'BEAR']
all_vix_bins = ['FALLING', 'STABLE', 'RISING']
all_age_bins = ['FRESH', 'ESTABLISHED', 'MATURE']

for regime in all_possible_regimes:
    for vix_bin in all_vix_bins:
        for age_bin in all_age_bins:
            cell_trades = merged_df[
                (merged_df['entry_regime_resolved'] == regime) &
                (merged_df['vix_momentum_bucket'] == vix_bin) &
                (merged_df['regime_age_bucket'] == age_bin)
            ]
            
            count = len(cell_trades)
            if count > 0:
                avg_ret = cell_trades['return_per_risk'].mean()
                std_ret = cell_trades['return_per_risk'].std()
                win_rate = (cell_trades['pnl_dollars'] > 0).mean()
                sharpe = avg_ret / std_ret if (std_ret > 0 and count > 1) else 0.0
                t_stat = avg_ret / (std_ret / np.sqrt(count)) if (std_ret > 0 and count > 1) else 0.0
            else:
                avg_ret = 0.0
                std_ret = 0.0
                win_rate = 0.0
                sharpe = 0.0
                t_stat = 0.0
            
            # Determine Seeding Multiplier under Stage-1 rules (0.90x to 1.10x)
            is_significant = (count >= 30) and (abs(t_stat) >= 1.96)
            
            multiplier = 1.00
            evidence = "Default"
            
            if is_significant:
                if regime == 'FRAGILE':
                    multiplier = 1.00
                    evidence = f"Significant, capped for Stage 1 ({count} trades, T={t_stat:.2f})"
                else:
                    multiplier = 0.90 + 0.20 * (sharpe - 0.2021) / (0.8574 - 0.2021)
                    multiplier = round(multiplier, 2)
                    evidence = f"{count} trades, T={t_stat:.2f}"
            else:
                evidence = f"No significance ({count} trades, T={t_stat:.2f})"
                
            seeding_records.append({
                'entry_regime': regime,
                'vix_momentum_bucket': vix_bin,
                'regime_age_bucket': age_bin,
                'trade_count': count,
                't_stat': round(t_stat, 4),
                'sharpe': round(sharpe, 4),
                'cte_multiplier': multiplier,
                'evidence': evidence,
                'last_updated': datetime.now()
            })

seeding_df = pd.DataFrame(seeding_records)

# 4. Drop and rebuild the lookup table in findb.duckdb sandbox schema
print("\nDropping existing sandbox.e1_cte_lookup table if exists...")
conn.execute("DROP TABLE IF EXISTS sandbox.e1_cte_lookup")

print("Rebuilding sandbox.e1_cte_lookup from the seeded dataframe...")
conn.execute("CREATE TABLE sandbox.e1_cte_lookup AS SELECT * FROM seeding_df")

print("\nSeeding completed successfully!")
conn.close()
