import duckdb
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from datetime import datetime, timedelta
from E1.core.config import DB_PATH

LOOKBACK_DAYS = 80  # For rolling 60d + 20d forward return window
FORWARD_WINDOW = 20
SIGNALS = [
    'sig_ma_crossover', 'sig_rs_3month', 'sig_sector_momentum', 'sig_ma_slope',
    'sig_rsi_oversold', 'sig_drawdown_recovery', 'sig_fundamental'
]
DECAY_THRESHOLD = 0.60
RECOVERY_THRESHOLD = 0.80
DECAY_STREAK = 30
RECOVERY_STREAK = 20

def compute_live_ic():
    con = duckdb.connect(DB_PATH)
    
    # 1. Load benchmark ICs from history (latest snapshot)
    benchmarks = con.execute("""
        SELECT signal, regime, ic 
        FROM refined.ic_history 
        WHERE computed_at = (SELECT MAX(computed_at) FROM refined.ic_history)
        AND gate = 'PASS'
    """).df()
    
    # Create a mapping for primary (regime) and secondary (ALL) benchmarks
    bench_map = {}
    for _, row in benchmarks.iterrows():
        bench_map[(row['signal'], row['regime'])] = row['ic']

    # 2. Get the latest date to process
    latest_date = con.execute("SELECT MAX(date) FROM refined.ensemble_daily_scores").fetchone()[0]
    if not latest_date:
        print("No data in ensemble_daily_scores.")
        return

    # 3. Pull data for the rolling window
    data_query = f"""
    WITH prices AS (
        SELECT ticker, date, close_price,
               LEAD(close_price, {FORWARD_WINDOW}) OVER (PARTITION BY ticker ORDER BY date) / close_price - 1 AS fwd_return
        FROM refined.daily_signals_ml
        WHERE date >= (SELECT CAST(MAX(date) AS DATE) - INTERVAL '{LOOKBACK_DAYS + FORWARD_WINDOW} days' FROM refined.ensemble_daily_scores)
    ),
    scores AS (
        SELECT s.ticker, s.date, r.regime,
               s.sig_ma_crossover, s.sig_rs_3month, s.sig_sector_momentum, s.sig_ma_slope,
               s.sig_rsi_oversold, s.sig_drawdown_recovery, s.sig_fundamental
        FROM refined.ensemble_daily_scores s
        JOIN refined.market_regime r ON s.date = r.date
    )
    SELECT s.*, p.fwd_return
    FROM scores s
    JOIN prices p ON s.ticker = p.ticker AND s.date = p.date
    WHERE p.fwd_return IS NOT NULL
    """
    df = con.execute(data_query).df()
    
    if df.empty:
        print("No overlapping data for scores and forward returns.")
        return

    # 4. Compute Rolling IC per signal/regime
    results = []
    
    # Filter for the last 60 trading days available in the dataframe
    available_dates = sorted(df['date'].unique())
    last_60_dates = available_dates[-60:]
    df_window = df[df['date'].isin(last_60_dates)]

    # We also need to loop through the 3 major regimes
    for regime in ['HEALTHY', 'FRAGILE', 'BEAR']:
        regime_df = df_window[df_window['regime'] == regime]
        if regime_df.empty:
            continue
            
        for sig in SIGNALS:
            clean = regime_df[[sig, 'fwd_return']].dropna()
            if len(clean) < 50: # Minimum population check
                continue
                
            ic, pval = spearmanr(clean[sig], clean['fwd_return'])
            
            # Fetch benchmarks
            primary_bench = bench_map.get((sig, regime), 0.01) # fallback to min threshold
            all_bench = bench_map.get((sig, 'ALL'), primary_bench)
            
            # 5. Determine status
            prev_status_query = f"""
            SELECT status, decay_streak, recovery_streak
            FROM refined.ic_monitor
            WHERE signal = '{sig}' AND date < '{latest_date}'
            ORDER BY date DESC LIMIT 1
            """
            prev = con.execute(prev_status_query).fetchone()
            
            prev_status = prev[0] if prev else 'HEALTHY'
            curr_decay_streak = prev[1] if prev else 0
            curr_recovery_streak = prev[2] if prev else 0
            
            is_decaying_now = (ic < DECAY_THRESHOLD * primary_bench) or (pval > 0.10)
            is_recovering_now = (ic > RECOVERY_THRESHOLD * primary_bench) and (pval <= 0.05)
            
            new_status = prev_status
            
            if is_decaying_now:
                curr_decay_streak += 1
                curr_recovery_streak = 0
            elif is_recovering_now:
                curr_recovery_streak += 1
                curr_decay_streak = 0
            else:
                curr_decay_streak = 0
                curr_recovery_streak = 0
                
            if curr_decay_streak >= DECAY_STREAK:
                new_status = 'DECAYING'
            elif curr_recovery_streak >= RECOVERY_STREAK:
                new_status = 'HEALTHY'
            elif is_decaying_now:
                new_status = 'WATCH'
            else:
                new_status = 'HEALTHY'
                
            results.append({
                'date': latest_date,
                'signal': sig,
                'regime': regime,
                'rolling_60d_ic': round(ic, 6),
                'rolling_pval': round(pval, 6),
                'backtest_ic': primary_bench,
                'all_regime_ic_ref': all_bench,
                'vs_backtest': round(ic / primary_bench, 4) if primary_bench != 0 else 0,
                'status': new_status,
                'decay_streak': curr_decay_streak,
                'recovery_streak': curr_recovery_streak,
                'n': len(clean)
            })

    # 6. Write to ic_monitor
    res_df = pd.DataFrame(results)
    if not res_df.empty:
        con.execute(f"DELETE FROM refined.ic_monitor WHERE date = '{latest_date}'")
        con.execute("INSERT INTO refined.ic_monitor SELECT * FROM res_df")
        print(f"Logged live IC for {len(res_df)} signal/regime combinations for {latest_date}.")
    
    con.close()

if __name__ == '__main__':
    compute_live_ic()
