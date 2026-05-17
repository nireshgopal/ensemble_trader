import duckdb
import json
import os
import sys
import pandas as pd
from datetime import datetime
from collections import defaultdict
from E1.core.config import DB_PATH

sys.path.insert(0, os.getcwd())

# Constants from E1 weight computation logic
IC_THRESHOLD = 0.01
PVAL_THRESHOLD = 0.05
MAX_SIGNAL_WEIGHT = 0.50 # GOVERNANCE: sig_fundamental capped at 0.50 per IC regularization policy.
# Raw IC-proportional weight was 0.775. Cap prevents single-signal fragility.
# Approved: 2026-05-16, Phase 5 weight review.
SHRINKAGE_LAMBDA = 0.3
OUTPUT_PATH = 'docs/signal_weights_CANDIDATE.json'

# Signal → cluster mapping
SIG_CLUSTER = {
    'sig_rs_3month':         'trend',
    'sig_ma_slope':          'trend',
    'sig_rs_12month':        'trend',
    'sig_rs_6month':         'trend',
    'sig_price_stage':       'trend',
    'sig_rsi_oversold':      'mean_reversion',
    'sig_drawdown_recovery': 'mean_reversion',
    'sig_fundamental':       'quality',
    'sig_52w_high':          'quality',
    'sig_volume':            'quality',
}

# Cluster → members mapping (inverse of SIG_CLUSTER)
CLUSTERS = defaultdict(list)
for sig, cluster in SIG_CLUSTER.items():
    CLUSTERS[cluster].append(sig)

CANONICAL_SIGNALS = list(SIG_CLUSTER.keys())


def apply_cap(weights, max_weight=MAX_SIGNAL_WEIGHT):
    """Redistribute weight from any signal exceeding max_weight to others."""
    for _ in range(10):  # iterate until stable
        excess = {s: w - max_weight for s, w in weights.items() if w > max_weight}
        if not excess:
            break
        total_excess = sum(excess.values())
        eligible = {s: w for s, w in weights.items() if w <= max_weight and w > 0}
        total_eligible = sum(eligible.values())
        for s in excess:
            weights[s] = max_weight
        if total_eligible > 0:
            for s, w in eligible.items():
                weights[s] += total_excess * (w / total_eligible)


def compute_quant_weights(ic_df, lambda_shrink=SHRINKAGE_LAMBDA, decay_mult=None):
    """Cluster-budget + IC-proportional + dead-cluster redistribution."""
    if decay_mult is None:
        decay_mult = {}
    surviving_series = ic_df[
        (ic_df['ic'] > IC_THRESHOLD) & (ic_df['pval'] < PVAL_THRESHOLD)
    ].set_index('signal')['ic']
    surviving = dict(surviving_series)

    # 1. Cluster budgets from surviving signals
    cluster_ic = {}
    for c, members in CLUSTERS.items():
        cluster_ic[c] = float(sum(surviving.get(s, 0) for s in members if s in surviving))
    total_ic = sum(cluster_ic.values())

    # 2. Redistribute budget from dead clusters to live ones
    if total_ic > 0:
        cluster_budgets = {c: v / total_ic for c, v in cluster_ic.items()}
    else:
        cluster_budgets = {c: 1 / len(CLUSTERS) for c in CLUSTERS}

    # 3. Intra-cluster shrinkage and normalization
    weights = {}
    for cluster, members in CLUSTERS.items():
        budget = cluster_budgets.get(cluster, 0)
        c_sigs = {s: surviving[s] for s in members if s in surviving}
        if not c_sigs:
            for m in members:
                weights[m] = 0.0
            continue

        c_sum = sum(c_sigs.values())
        n = len(c_sigs)
        for sig, val in c_sigs.items():
            ic_prop = val / c_sum if c_sum > 0 else 1.0 / n
            raw_w = budget * (lambda_shrink * ic_prop + (1 - lambda_shrink) * (1.0 / n))
            weights[sig] = raw_w * decay_mult.get(sig, 1.0)

    # 4. Fill missing signals with 0.0
    for sig in CANONICAL_SIGNALS:
        if sig not in weights:
            weights[sig] = 0.0

    # 5. Apply 50% max-cap redistribution
    apply_cap(weights)

    # 6. Final Normalization
    total_w = sum(weights.values())
    if total_w > 0:
        for sig in weights:
            weights[sig] /= total_w
            
    return weights


if __name__ == '__main__':
    con = duckdb.connect(DB_PATH)

    # Verify ic_history has data
    latest = con.execute(
        "SELECT MAX(computed_at) AS latest FROM refined.ic_history"
    ).fetchone()[0]
    if latest is None:
        print("ERROR: refined.ic_history is empty. Run compute_signal_ic.py first.")
        sys.exit(1)

    print(f"Reading IC data from refined.ic_history (latest snapshot: {latest})")

    version_id = f"v{datetime.now().strftime('%Y%m%d_%H%M')}"
    
    output = {
        "_metadata": {
            "version": version_id,
            "generated_at": str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "source": "refined.ic_history",
            "ic_snapshot_date": str(latest),
            "method": "cluster-budget + IC-proportional + dead-cluster redistribution"
        }
    }

    for regime in ['HEALTHY', 'FRAGILE', 'BEAR']:
        ic_df = con.execute(f"""
            SELECT signal, ic, pval
            FROM refined.ic_history
            WHERE computed_at = (SELECT MAX(computed_at) FROM refined.ic_history)
            AND regime = '{regime}'
            AND window_days = 0
            AND NOT signal LIKE '__cluster__%'
        """).df()

        if ic_df.empty:
            print(f"  WARNING: No IC data for regime {regime} — using equal weights")
            weights = {sig: 1.0 / len(CANONICAL_SIGNALS) for sig in CANONICAL_SIGNALS}
        else:
            # Load decay multipliers for the current regime
            decay_df = con.execute(f"""
                SELECT signal, status 
                FROM refined.ic_monitor 
                WHERE date = (SELECT MAX(date) FROM refined.ic_monitor)
                AND regime = '{regime}'
            """).df()
            decay_mult = {row.signal: 0.5 if row.status == 'DECAYING' else 1.0 
                         for row in decay_df.itertuples()}
            
            weights = compute_quant_weights(ic_df, decay_mult=decay_mult)

        output[regime] = {
            sig: {"weight": round(w, 4), "direction": 1}
            for sig, w in weights.items()
        }

        active = {s: w for s, w in weights.items() if w > 0}
        print(f"  [{regime}] {len(active)} active signals: " +
              ", ".join(f"{s}={w:.1%}" for s, w in sorted(active.items(), key=lambda x: -x[1])))
    con.close()

    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nDONE: Weights regenerated from ic_history -> {OUTPUT_PATH}")
    print(f"   Generated at: {output['_metadata']['generated_at']}")
    print(f"   IC snapshot:  {output['_metadata']['ic_snapshot_date']}")
