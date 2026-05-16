"""
refine/backtest/signal_votes.py

Regime-conditional ensemble scoring engine.

Three-regime weight sets computed from IC data:
  HEALTHY — low conviction (weak ICs, near-neutral scores)
  FRAGILE — mean reversion signals dominate (RSI bounce, drawdown recovery)
  BEAR    — full contrarian (trend signals inverted)

Public API:
  load_regime_weights(ic_summary_path) -> {regime: (weights, directions)}
  compute_votes(row, breadth_pct)      -> {signal: vote}
  compute_dominant_cluster(votes, weights) -> (cluster_name, dominance_pct)
  aggregate_score(votes, regime_weights, row) -> float 0.0-1.0

Called by scorer.py. Weights loaded once at startup.
"""

import pandas as pd
import numpy as np
import os

# Canonical signal names — single source of truth
CANONICAL_SIGNALS = [
    'sig_rs_3month',
    'sig_ma_slope',
    'sig_rsi_oversold',
    'sig_drawdown_recovery',
    'sig_fundamental',
]

# Cluster definitions
CLUSTERS = {
    'trend':          ['sig_rs_3month', 'sig_ma_slope'],
    'mean_reversion': ['sig_rsi_oversold', 'sig_drawdown_recovery'],
    'quality':        ['sig_fundamental'],
}

# Legacy CSV name mapping
_CSV_NAME_FIX = {'eps_surprise': 'sig_fundamental'}


# Maximum weight any single signal can hold (prevents concentration risk)
MAX_SIGNAL_WEIGHT = 0.50

# BEAR breadth-conditional tiers (set by load_regime_weights at startup)
_BEAR_BREADTH_TIERS = None


def _compute_weights_for_regime(regime_df, lambda_shrink=0.3):
    """
    Given a DataFrame of IC rows for ONE regime, compute weights and directions.
    Returns: (weights_dict, directions_dict)

    Dual gate: |IC| > 0.01 AND pval < 0.05.
    Cluster-budgeted shrinkage within each cluster.
    Direction derived from IC sign (positive IC -> +1, negative -> -1).
    Max weight per signal capped at MAX_SIGNAL_WEIGHT (50%).
    """
    # Filter to signals (not metadata rows)
    regime_df = regime_df[~regime_df['signal'].str.startswith('__')].copy()

    # Drop rows with NaN IC
    regime_df = regime_df.dropna(subset=['ic'])

    # Dual gate
    passing = regime_df[(regime_df['ic'] > 0.01) & (regime_df['pval'] < 0.05)]

    # Directions always +1 — regime behavior comes from cluster budget weights only,
    # NOT from inverting vote direction. A negative IC means the signal is weak in
    # that regime; the weight allocation already handles this via the dual gate.
    directions = {sig: 1 for sig in CANONICAL_SIGNALS}

    if passing.empty:
        return (
            {sig: 0.0 for sig in CANONICAL_SIGNALS},
            {sig: 1 for sig in CANONICAL_SIGNALS}
        )

    surviving = passing.set_index('signal')['ic']

    # Cluster budgets from absolute IC
    cluster_ic = {}
    for cluster, members in CLUSTERS.items():
        cluster_ic[cluster] = sum(abs(surviving.get(s, 0)) for s in members if s in surviving.index)

    total = sum(cluster_ic.values())
    if total > 0:
        max_c = max(cluster_ic.values())
        min_c = min(cluster_ic.values())
        if max_c > 0 and (max_c - min_c) / max_c < 0.10:
            cluster_budgets = {c: 1/3 for c in CLUSTERS}
        else:
            cluster_budgets = {c: v / total for c, v in cluster_ic.items()}
    else:
        cluster_budgets = {c: 1/3 for c in CLUSTERS}

    # Intra-cluster shrinkage weights
    weights = {}
    for cluster, members in CLUSTERS.items():
        budget = cluster_budgets.get(cluster, 0)
        cluster_sigs = {s: abs(surviving[s]) for s in members if s in surviving.index}

        if not cluster_sigs:
            for m in members:
                weights[m] = 0.0
            continue

        cluster_ic_sum = sum(cluster_sigs.values())
        n = len(cluster_sigs)
        for sig, ic_val in cluster_sigs.items():
            ic_prop = ic_val / cluster_ic_sum if cluster_ic_sum > 0 else 1.0 / n
            equal_w = 1.0 / n
            w = lambda_shrink * ic_prop + (1 - lambda_shrink) * equal_w
            weights[sig] = budget * w

    # ── Weight Cap: no single signal above MAX_SIGNAL_WEIGHT ─────────────
    _apply_weight_cap(weights)

    # Fill missing signals with 0
    for sig in CANONICAL_SIGNALS:
        if sig not in weights:
            weights[sig] = 0.0
        if sig not in directions:
            directions[sig] = 1

    return weights, directions


def _apply_weight_cap(weights):
    """Cap any signal at MAX_SIGNAL_WEIGHT and redistribute excess proportionally."""
    for _ in range(5):  # Iterate to handle cascading overflows
        excess = 0.0
        capped = set()
        for sig, w in weights.items():
            if w > MAX_SIGNAL_WEIGHT:
                excess += w - MAX_SIGNAL_WEIGHT
                weights[sig] = MAX_SIGNAL_WEIGHT
                capped.add(sig)

        if excess == 0:
            break

        # Redistribute excess proportionally to non-capped, non-zero signals
        eligible = {s: w for s, w in weights.items() if w > 0 and s not in capped}
        eligible_total = sum(eligible.values())
        if eligible_total > 0:
            for sig, w in eligible.items():
                weights[sig] += excess * (w / eligible_total)
        else:
            # No eligible signals — distribute equally to all capped signals
            for sig in capped:
                weights[sig] = 1.0 / len(capped) if capped else 0.0


def _safe_default_weights():
    """Equal weights across all 7 signals with direction +1. Used when regime is unknown."""
    n = len(CANONICAL_SIGNALS)
    return (
        {sig: 1.0 / n for sig in CANONICAL_SIGNALS},
        {sig: 1 for sig in CANONICAL_SIGNALS}
    )


import json

def load_regime_weights(path, lambda_shrink=0.3):
    """
    Loads weights and directions from either a JSON config or an IC summary CSV.

    Args:
        path: Path to signal_weights.json OR ic_summary.csv
        lambda_shrink: Used if computing from CSV

    Returns: dict of {regime: (weights_dict, directions_dict)}
    """
    safe = _safe_default_weights()

    if not os.path.exists(path):
        print(f"Warning: {path} not found. Using equal weights fallback.")
        return {r: safe for r in ['HEALTHY', 'FRAGILE', 'BEAR', 'SAFE_DEFAULT']}

    # --- GOVERNANCE INTERLOCK: Weights Mode Check ---
    # We use a late import or direct check of config to avoid circular imports
    from E1.core import config
    if getattr(config, 'WEIGHTS_MODE', None) == "frozen":
        if "experimental" in path.lower():
            raise RuntimeError(f"GOVERNANCE VIOLATION: WEIGHTS_MODE is 'frozen' but path is {path}. Aborting.")

    # --- Case 1: Load from JSON (Primary Source of Truth for Scorer) ---
    if path.lower().endswith('.json'):
        with open(path, 'r') as f:
            data = json.load(f)
        
        global _BEAR_BREADTH_TIERS
        regime_weights = {}
        for regime, sig_data in data.items():
            # Extract breadth tiers if present (BEAR only)
            breadth_tiers = sig_data.get('_breadth_tiers')
            if breadth_tiers is not None and regime == 'BEAR':
                _BEAR_BREADTH_TIERS = breadth_tiers
            # Filter to signal entries only (skip keys starting with '_')
            signals = {s: d for s, d in sig_data.items()
                       if isinstance(d, dict) and 'weight' in d}
            weights = {s: d['weight'] for s, d in signals.items()}
            directions = {s: d['direction'] for s, d in signals.items()}
            # Fill missing signals with 0
            for sig in CANONICAL_SIGNALS:
                if sig not in weights: weights[sig] = 0.0
                if sig not in directions: directions[sig] = 1
            regime_weights[regime] = (weights, directions)
        
        if 'SAFE_DEFAULT' not in regime_weights:
            regime_weights['SAFE_DEFAULT'] = safe

        if _BEAR_BREADTH_TIERS:
            # Weights loaded (Silent for independence)
            pass
        return regime_weights

    # --- Case 2: Compute from CSV (Generator Mode) ---
    df = pd.read_csv(path)
    df['signal'] = df['signal'].map(lambda s: _CSV_NAME_FIX.get(s, s))

    if 'regime' not in df.columns or 'gate' not in df.columns:
        print(f"Warning: {path} lacks regime columns. Using single weight set.")
        weights, dirs = _compute_weights_for_regime(df, lambda_shrink)
        return {r: (weights, dirs) for r in ['HEALTHY', 'FRAGILE', 'BEAR', 'SAFE_DEFAULT']}

    regime_weights = {'SAFE_DEFAULT': safe}
    for regime in ['HEALTHY', 'FRAGILE', 'BEAR']:
        regime_df = df[df['regime'] == regime]
        if regime_df.empty:
            regime_weights[regime] = safe
            continue
        w, d = _compute_weights_for_regime(regime_df, lambda_shrink)
        regime_weights[regime] = (w, d)

    return regime_weights


# Backward-compatible wrapper (used by backtest scripts)
def load_weights(ic_summary_path, lambda_shrink=0.3):
    """Backward-compatible: returns SAFE_DEFAULT weights as (weights, directions)."""
    regime_weights = load_regime_weights(ic_summary_path, lambda_shrink)
    return regime_weights.get('SAFE_DEFAULT', _safe_default_weights())


def compute_votes(row, breadth_pct=0.6, sma200_floor=0.25):
    """
    Computes -1.0 to +1.0 votes for each signal based on row data.
    Votes are ALWAYS computed the same way regardless of regime.
    Direction correction is applied in aggregate_score() based on regime.
    """
    votes = {}

    close = row.get('close_price')
    s20 = row.get('sma_20')
    s50 = row.get('sma_50')
    s200 = row.get('sma_200')

    # S1: MA Crossover (Retired in Phase 5 - Zero Weight)

    # S2: 3-Month RS
    rs = row.get('rs_vs_spy_63d')
    if pd.notna(rs):
        votes['sig_rs_3month'] = float(np.clip(rs / 12.0, -1.0, 1.0))
    else:
        votes['sig_rs_3month'] = 0.0

    # S4: MA Slope
    slope = row.get('ma_slope_pct')
    if pd.notna(slope):
        votes['sig_ma_slope'] = float(np.clip(slope / 0.05, -1.0, 1.0))
    else:
        votes['sig_ma_slope'] = 0.0

    # ── Dampeners for S5 and S6 ──────────────────────────────────────────
    vol = row.get('volume')
    vol20 = row.get('vol_20d_avg')
    vol_conf = min(vol / vol20, 1.5) / 1.5 if (pd.notna(vol) and pd.notna(vol20) and vol20 > 0) else 1.0
    sma200_factor = max(sma200_floor, close / s200) if (_all_valid(close, s200) and s200 > 0) else 1.0
    breadth_mod = max(0.5, breadth_pct)

    # S5: RSI Oversold Bounce (Hardened BEAR Logic)
    rsi = row.get('rsi_14')
    if pd.notna(rsi):
        if rsi > 75:
            votes['sig_rsi_oversold'] = -0.5  # Overextended Penalty
        elif rsi > 65:
            votes['sig_rsi_oversold'] = 0.0
        elif rsi < 35:
            votes['sig_rsi_oversold'] = 1.0   # Genuinely Oversold
        else:
            # Linear scaling for intermediate 35-65 range
            base = (65.0 - rsi) / 65.0
            votes['sig_rsi_oversold'] = float(np.clip(base * vol_conf * sma200_factor * breadth_mod, 0.0, 1.0))
    else:
        votes['sig_rsi_oversold'] = 0.0

    # S6: Drawdown Recovery (Hardened 5% Floor)
    dd = row.get('drawdown_52w')
    if pd.notna(dd):
        if dd > -0.05:
            votes['sig_drawdown_recovery'] = 0.0  # < 5% from High is ignored
        else:
            base = max(dd, -0.35) / -0.35
            votes['sig_drawdown_recovery'] = float(np.clip(base * vol_conf * sma200_factor * breadth_mod, 0.0, 1.0))
    else:
        votes['sig_drawdown_recovery'] = 0.0

    # S7: PEAD
    # Fix A (2026-05-15): When PEAD window is stale (days > 60) or no earnings data,
    # abstain (None) rather than vote 0.0. A 0.0 vote with 50% weight compresses the
    # ensemble score of non-PEAD names; None drops sig_fundamental from the active_weight
    # denominator entirely, making the remaining signals carry 100% of the weight.
    surprise = row.get('eps_surprise')
    days = row.get('days_since_earnings')
    if pd.notna(surprise) and surprise != 0.0:
        if pd.notna(days) and 0 <= days <= 60:
            decay = 1.0 - (days / 60.0)
            raw = surprise * decay * 10
            votes['sig_fundamental'] = float(np.clip(raw, -1.0, 1.0))
        else:
            votes['sig_fundamental'] = None  # Stale: abstain, not zero
    else:
        votes['sig_fundamental'] = None  # No data: abstain, not zero

    # S8: Earnings Acceleration (Retired in Phase 5 - Zero Weight)

    return votes


def compute_dominant_cluster(votes, weights):
    """
    Returns the cluster name that contributed the most weighted absolute signal
    and its percentage contribution to the total absolute weighted vote.
    
    Cluster names are returned as 'Trend', 'Reversion', 'Quality'
    to match V1.3 specifications.
    """
    # Map internal cluster keys to V1.3 display names
    display_map = {
        'trend':          'Trend',
        'mean_reversion': 'Reversion',
        'quality':        'Quality'
    }
    
    cluster_contributions = {}
    total_abs_contribution = 0.0

    for cluster_key, signals in CLUSTERS.items():
        # Contribution is abs(vote) * weight
        # This measures which cluster is 'shouting the loudest' regardless of direction
        contrib = sum(
            abs(votes.get(sig, 0.0)) * weights.get(sig, 0.0)
            for sig in signals
            if votes.get(sig) is not None
        )
        display_name = display_map.get(cluster_key, cluster_key.capitalize())
        cluster_contributions[display_name] = contrib
        total_abs_contribution += contrib

    if total_abs_contribution == 0:
        return 'None', 0.0

    dominant = max(cluster_contributions, key=cluster_contributions.get)
    dominance_pct = cluster_contributions[dominant] / total_abs_contribution

    return dominant, round(dominance_pct, 4)


def aggregate_score(votes, regime_weights, row=None):
    """
    Regime-conditional IC-weighted aggregation.

    Selects weight/direction set based on current market regime from row.
    Applies Layer 4 multipliers (sentiment, analyst, EPS revision).
    Returns float in [0.0, 1.0] (0.5 = neutral).
    """
    # Determine regime
    regime = 'SAFE_DEFAULT'
    if row is not None:
        regime = row.get('regime') or 'SAFE_DEFAULT'

    # Get regime-specific weights and directions
    if isinstance(regime_weights, dict) and any(k in regime_weights for k in ['HEALTHY', 'FRAGILE', 'BEAR', 'SAFE_DEFAULT']):
        # Regime-conditional format: {regime: (weights, directions)}
        weights, directions = regime_weights.get(regime, regime_weights.get('SAFE_DEFAULT', _safe_default_weights()))
    elif isinstance(regime_weights, tuple) and len(regime_weights) == 2:
        # Old format: (weights, directions) tuple
        weights, directions = regime_weights
    else:
        # Fallback
        weights = regime_weights
        directions = {sig: 1 for sig in CANONICAL_SIGNALS}

    # ── BEAR breadth-conditional tier override ────────────────────────────
    if regime == 'BEAR' and _BEAR_BREADTH_TIERS and row is not None:
        breadth = row.get('breadth_pct')
        if breadth is not None and pd.notna(breadth):
            for tier in _BEAR_BREADTH_TIERS:
                if breadth <= tier['max_breadth']:
                    weights = {sig: tier['weights'].get(sig, 0.0)
                               for sig in CANONICAL_SIGNALS}
                    break

    total_val = 0.0
    active_weight = 0.0

    for sig, w in weights.items():
        if w == 0.0:
            continue
        v = votes.get(sig, 0.0)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue

        # Apply direction correction from regime-specific IC sign
        direction = directions.get(sig, 1)
        total_val += v * w * direction
        active_weight += w

    if active_weight == 0:
        return 0.5  # Neutral

    # Base score in [-1, +1]
    score = total_val / active_weight

    # ── Layer 4 Multipliers ──────────────────────────────────────────────
    if row is not None:
        sentiment = row.get('final_sentiment_factor')
        if pd.notna(sentiment):
            if sentiment >= 0.5:
                score *= 1.10
            elif sentiment <= -0.5:
                score *= 0.90

        analyst_up = row.get('analyst_upgrade_30d', False)
        analyst_down = row.get('analyst_downgrade_30d', False)
        if analyst_down:
            score *= 0.90
        elif analyst_up:
            score *= 1.10

        eps_change = row.get('eps_estimate_30d_change')
        if pd.notna(eps_change):
            eps_change = float(eps_change)
            if eps_change > 0.03:
                score *= 1.10
            elif eps_change < -0.03:
                score *= 0.90

    # Rescale [-1, +1] → [0, 1]
    return round((score + 1.0) / 2.0, 4)


def _all_valid(*vals):
    for v in vals:
        if v is None:
            return False
        try:
            if np.isnan(float(v)):
                return False
        except (TypeError, ValueError):
            return False
    return True


if __name__ == '__main__':
    regime_weights = load_regime_weights('docs/signal_weights.json')

    test_row = {
        'close_price': 150, 'sma_20': 145, 'sma_50': 140, 'sma_200': 130,
        'rs_vs_spy_63d': 5.0, 'sector_rank': 2, 'ma_slope_pct': 0.02,
        'volume': 1500000, 'vol_20d_avg': 1000000, 'rsi_14': 35, 'drawdown_52w': -0.15,
        'eps_surprise': 0.1, 'days_since_earnings': 10,
        'final_sentiment_factor': 0.3, 'eps_estimate_30d_change': 0.05,
    }

    votes = compute_votes(test_row, breadth_pct=0.8)
    print(f"\nVotes (same across all regimes):")
    for k, v in sorted(votes.items()):
        print(f"  {k:35s} = {v:+.4f}")

    for regime in ['HEALTHY', 'FRAGILE', 'BEAR']:
        test_row['regime'] = regime
        score = aggregate_score(votes, regime_weights, row=test_row)
        print(f"\n  [{regime:8s}] Ensemble Score: {score:.4f}")
