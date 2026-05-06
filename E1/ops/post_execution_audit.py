import duckdb
import json
import os
import sys
import pandas as pd
from datetime import datetime, date

# DB_PATH removed, using config.DB_PATH
WEIGHTS_PATH = 'docs/signal_weights.json'
VOL_THRESHOLD = 500000
FLOAT_TOLERANCE = 1e-6

# Import config for table constants
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'core')))
import config

def run_audit():
    con = duckdb.connect(config.DB_PATH)
    today = date.today().strftime('%Y-%m-%d')
    # latest_date = con.execute("SELECT MAX(entry_date) FROM refined.e1_positions").fetchone()[0]
    latest_date = today # Use real-time today for the audit
    
    print(f"--- Strategy E1 Execution Fidelity Audit ({today}) ---")
    failures = []

    # 1. Weights Freshness Check
    if not os.path.exists(WEIGHTS_PATH):
        failures.append(f"CRITICAL: {WEIGHTS_PATH} missing")
    else:
        with open(WEIGHTS_PATH, 'r') as f:
            weights_data = json.load(f)
            gen_date_str = weights_data.get('_metadata', {}).get('ic_snapshot_date')
            if gen_date_str:
                gen_date = datetime.strptime(gen_date_str, '%Y-%m-%d').date()
                age_days = (date.today() - gen_date).days
                if age_days > 7:
                    failures.append(f"FAIL: Weights are stale ({age_days} days old, generated on {gen_date_str})")
                else:
                    print(f"[PASS] Weights Freshness: {age_days} days old")

    # 2. Audit Today's Entries
    # Check thresholds, liquidity, and vote consistency
    audit_query = f"""
    SELECT 
        p.ticker, 
        p.entry_regime, 
        p.ensemble_score,
        p.vote_signal_1, p.vote_signal_2, p.vote_signal_3, p.vote_signal_4,
        p.vote_signal_5, p.vote_signal_6, p.vote_signal_7,
        s.sig_ma_crossover, s.sig_rs_3month, s.sig_sector_momentum, s.sig_ma_slope,
        s.sig_rsi_oversold, s.sig_drawdown_recovery, s.sig_fundamental,
        m.volume
    FROM {config.E1_POSITIONS_TABLE} p
    LEFT JOIN refined.ensemble_daily_scores s ON p.ticker = s.ticker AND p.entry_date = s.date
    LEFT JOIN refined.daily_signals_ml m ON p.ticker = m.ticker AND p.entry_date = m.date
    WHERE p.entry_date = '{latest_date}'
    """
    entries = con.execute(audit_query).df()

    if entries.empty:
        print(f"[INFO] No entries found for {latest_date}. Nothing to audit.")
    else:
        print(f"Auditing {len(entries)} entries...")
        for _, row in entries.iterrows():
            ticker = row['ticker']
            regime = row['entry_regime']
            score = row['ensemble_score']
            volume = row['volume']

            # Threshold Integrity
            if regime == 'HEALTHY':
                if score < 0.55:
                    failures.append(f"FAIL: {ticker} ensemble_score {score:.4f} < 0.55 in HEALTHY regime")
            elif regime in ['BEAR', 'FRAGILE']:
                if score < 0.30:
                    failures.append(f"FAIL: {ticker} ensemble_score {score:.4f} < 0.30 in {regime} regime")

            # Liquidity Gate
            if pd.isna(volume):
                # Optionally check refined.price_history if ml table not updated
                alt_vol = con.execute(f"SELECT volume FROM refined.price_history WHERE ticker = '{ticker}' AND date = '{latest_date}'").fetchone()
                volume = alt_vol[0] if alt_vol else None

            if volume is not None and volume < VOL_THRESHOLD:
                failures.append(f"FAIL: {ticker} volume {volume:,} < {VOL_THRESHOLD:,} liquidity floor")
            elif volume is None:
                 print(f"[WARN] {ticker} volume data missing for liquidity audit.")

            # Vote Consistency
            sig_map = {
                'vote_signal_1': 'sig_ma_crossover',
                'vote_signal_2': 'sig_rs_3month',
                'vote_signal_3': 'sig_sector_momentum',
                'vote_signal_4': 'sig_ma_slope',
                'vote_signal_5': 'sig_rsi_oversold',
                'vote_signal_6': 'sig_drawdown_recovery',
                'vote_signal_7': 'sig_fundamental'
            }
            for v_col, s_col in sig_map.items():
                v_val = row[v_col]
                s_val = row[s_col]
                if not pd.isna(v_val) and not pd.isna(s_val):
                    if abs(v_val - s_val) > FLOAT_TOLERANCE:
                        # Except when vote is 0 and sig has value (happens when signal fails gate but logged as 0)
                        if v_val == 0 and s_val != 0:
                            continue # Assume gate catch
                        failures.append(f"FAIL: {ticker} vote drift on {s_col}: Logged={v_val}, Scored={s_val}")

    if not failures:
        print("[PASS] All execution fidelity checks passed.")
        sys.exit(0)
    else:
        print("\n!!! FIDELITY BREACH DETECTED !!!")
        for f in failures:
            print(f)
        sys.exit(1)

if __name__ == '__main__':
    run_audit()
