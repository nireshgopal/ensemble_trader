import duckdb
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
import os
import sys
import datetime
import json
from E1.core.config import DB_PATH

sys.path.insert(0, os.getcwd())


SIGNALS = [
    'sig_ma_crossover',
    'sig_rs_3month',
    'sig_sector_momentum',
    'sig_ma_slope',
    'sig_rsi_oversold',
    'sig_drawdown_recovery',
    'sig_fundamental',
]
HORIZONS = [5, 10, 20, 40]

CLUSTERS = {
    'trend':          ['sig_ma_crossover', 'sig_rs_3month', 'sig_sector_momentum', 'sig_ma_slope'],
    'mean_reversion': ['sig_rsi_oversold', 'sig_drawdown_recovery'],
    'quality':        ['sig_fundamental'],
}

def _compute_ic_metrics(df, signals, horizons):
    """Core logic to compute IC, regime-conditional IC, and cluster budgets."""
    results = []

    # ── STEP 1: Multi-Horizon IC — pick best horizon per signal ──────────────
    print("\n-- Overall IC (Best Horizon Selection) --")
    for sig in signals:
        best_ic, best_h, best_pval, best_n = 0.0, 20, 1.0, 0
        for h in horizons:
            col = f'fwd_return_{h}d'
            if col not in df.columns:
                continue
            sub = df[[sig, col]].dropna()
            if len(sub) < 100:
                continue
            ic, pval = spearmanr(sub[sig], sub[col])
            if abs(ic) > abs(best_ic):
                best_ic, best_h, best_pval = ic, h, pval
                best_n = len(sub)

        # Dual gate: IC > 0.01 AND p-value < 0.05 (long-only: positive IC required)
        gate = 'PASS' if (best_ic > 0.01 and best_pval < 0.05) else 'FAIL'

        results.append({
            'signal': sig,
            'regime': 'ALL',
            'ic': round(best_ic, 6),
            'pval': round(best_pval, 6),
            'best_horizon_days': best_h,
            'n': best_n,
            'gate': gate
        })
        print(f"  {sig:30s} IC={best_ic:+.5f}  p={best_pval:.5f}  horizon={best_h}d  [{gate}]  n={best_n:,}")

    # ── STEP 2: Regime-Conditional IC ────────────────────────────────────────
    print("\n-- Regime-Conditional IC --")
    for regime in ['HEALTHY', 'FRAGILE', 'BEAR']:
        rdf = df[df['regime'] == regime]
        if len(rdf) < 100:
            print(f"  WARNING: Regime {regime} has {len(rdf)} rows (< 100) — skipping")
            continue
        for sig in signals:
            sub = rdf[['fwd_return_20d', sig]].dropna()
            if len(sub) < 100:
                continue
            ic, pval = spearmanr(sub[sig], sub['fwd_return_20d'])
            gate = 'PASS' if (ic > 0.01 and pval < 0.05) else 'FAIL'
            results.append({
                'signal': sig,
                'regime': regime,
                'ic': round(ic, 6),
                'pval': round(pval, 6),
                'best_horizon_days': 20,
                'n': len(sub),
                'gate': gate
            })

    # ── STEP 3: Cluster Budgets (per-regime) ────────────────────────────────
    print("\n-- Cluster Budgets (per-regime) --")
    for regime_label in ['ALL', 'HEALTHY', 'FRAGILE', 'BEAR']:
        regime_ic = {r['signal']: r['ic'] for r in results
                     if r['regime'] == regime_label and r['gate'] == 'PASS'
                     and not r['signal'].startswith('__')}

        cluster_ic = {}
        for cluster, sigs in CLUSTERS.items():
            cluster_ic[cluster] = sum(regime_ic.get(s, 0) for s in sigs if s in regime_ic)

        total_cluster_ic = sum(cluster_ic.values())
        if total_cluster_ic > 0:
            max_c = max(cluster_ic.values())
            min_c = min(cluster_ic.values())
            if max_c > 0 and (max_c - min_c) / max_c < 0.10:
                budgets = {c: 1/3 for c in CLUSTERS}
            else:
                budgets = {c: v / total_cluster_ic for c, v in cluster_ic.items()}
        else:
            budgets = {c: 1/3 for c in CLUSTERS}

        print(f"  [{regime_label:8s}] " + "  ".join(f"{c}={b:.3f}" for c, b in budgets.items()))

        for cluster, budget in budgets.items():
            results.append({
                'signal': f'__cluster__{cluster}',
                'regime': regime_label,
                'ic': round(budget, 5),
                'pval': None,
                'best_horizon_days': None,
                'n': None,
                'gate': 'CLUSTER_BUDGET'
            })
    
    return results

def run_ic_computation():
    con = duckdb.connect(DB_PATH)

    print("Fetching pre-computed signals from refined.ensemble_daily_scores (full history)...")
    df = con.execute("""
        SELECT
            e.ticker,
            e.date,
            e.close_price,
            e.regime,
            e.sig_ma_crossover,
            e.sig_rs_3month,
            e.sig_sector_momentum,
            e.sig_ma_slope,
            e.sig_rsi_oversold,
            e.sig_drawdown_recovery,
            e.sig_fundamental
        FROM refined.ensemble_daily_scores e
        WHERE e.close_price IS NOT NULL
          AND e.close_price > 1.0
    """).df()

    print(f"Loaded {len(df):,} rows, {df['ticker'].nunique():,} tickers.")
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(['ticker', 'date'])
    for h in HORIZONS:
        df[f'fwd_return_{h}d'] = df.groupby('ticker')['close_price'].shift(-h) / df['close_price'] - 1

    df_full = df.dropna(subset=['fwd_return_20d'])
    
    # --- FULL HISTORY IC ---
    results_full = _compute_ic_metrics(df_full, SIGNALS, HORIZONS)
    ic_df = pd.DataFrame(results_full)
    ic_df.to_csv('docs/ic_summary.csv', index=False)
    print(f"\nSaved to docs/ic_summary.csv ({len(ic_df)} rows)")

    # --- Persist IC snapshot to ic_history ---
    today = datetime.date.today()
    ic_history_rows = []
    for r in results_full:
        ic_history_rows.append({
            'computed_at': today,
            'signal': r['signal'],
            'regime': r['regime'],
            'ic': r['ic'],
            'pval': r['pval'],
            'best_horizon_days': r['best_horizon_days'],
            'n': r['n'],
            'gate': r['gate'],
            'window_days': 0 # Full history (0 denotes lifetime)
        })
    
    con_write = duckdb.connect(DB_PATH) # Open new connection for writes to be safe
    ic_history_df = pd.DataFrame(ic_history_rows)
    con_write.execute("INSERT OR REPLACE INTO refined.ic_history SELECT * FROM ic_history_df")
    print(f"IC history snapshot written: {len(ic_history_df)} rows for {today}")

    # --- 90-day Rolling IC ---
    cutoff = today - datetime.timedelta(days=90)
    df_recent = df_full[df_full['date'] >= pd.Timestamp(cutoff)]
    if len(df_recent) > 100:
        results_rolling = _compute_ic_metrics(df_recent, SIGNALS, HORIZONS)
        rolling_rows = []
        for r in results_rolling:
            rolling_rows.append({
                'computed_at': today,
                'signal': r['signal'],
                'regime': r['regime'],
                'ic': r['ic'],
                'pval': r['pval'],
                'best_horizon_days': r['best_horizon_days'],
                'n': r['n'],
                'gate': r['gate'],
                'window_days': 90
            })
        rolling_df = pd.DataFrame(rolling_rows)
        con_write.execute("INSERT OR REPLACE INTO refined.ic_history SELECT * FROM rolling_df")
        print(f"Rolling 90-day IC snapshot written: {len(rolling_df)} rows")
    else:
        print(f"Insufficient data for 90-day rolling IC ({len(df_recent)} rows). Skipping.")

    # --- Regenerate signal_weights.json and Persist to weights_history ---
    from refine.engine.signal_votes import load_regime_weights, CANONICAL_SIGNALS
    rw = load_regime_weights('docs/signal_weights.json')
    weights_doc = {}
    weights_history_rows = []

    for regime in ['SAFE_DEFAULT', 'HEALTHY', 'FRAGILE', 'BEAR']:
        w, d = rw[regime]
        
        # ── Regime Processing ──────────────────────────────────
        weights_doc[regime] = {
            sig: {'weight': round(w.get(sig, 0), 4),
                  'weight_pct': f'{round(w.get(sig, 0) * 100, 1)}%',
                  'direction': d.get(sig, 1)}
            for sig in CANONICAL_SIGNALS if w.get(sig, 0) > 0
        }
        # For history, we store ALL signals (including 0.0 weights)
        for sig in CANONICAL_SIGNALS:
            weights_history_rows.append({
                'computed_at': today,
                'regime': regime,
                'signal': sig,
                'weight': round(w.get(sig, 0), 4),
                'weight_pct': round(w.get(sig, 0) * 100, 1),
                'direction': d.get(sig, 1)
            })

    with open('docs/signal_weights_CANDIDATE.json', 'w') as f:
        json.dump(weights_doc, f, indent=2)
    print("Saved to docs/signal_weights_CANDIDATE.json")

    weights_history_df = pd.DataFrame(weights_history_rows)
    con_write.execute("INSERT OR REPLACE INTO refined.weights_history SELECT * FROM weights_history_df")
    print(f"Weights history snapshot written: {len(weights_history_df)} rows")

    con.close()
    con_write.close()

if __name__ == '__main__':
    run_ic_computation()
