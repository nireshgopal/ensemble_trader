"""
E1 Strategy Scorer — Hardened V1.4 logic.
This script focuses exclusively on the Ensemble Scoring Engine used by Strategy E1.
"""
import pandas as pd
import numpy as np
import datetime
import os
from . import config
from . import signal_votes

# Initialize Ensemble Weights — regime-conditional (Build 4)
# Primary source: signal_weights.json
WEIGHTS_JSON_PATH = os.path.join('docs', 'signal_weights.json')
ENSEMBLE_REGIME_WEIGHTS = signal_votes.load_regime_weights(WEIGHTS_JSON_PATH)

# Sectors where Piotroski F-Score is structurally inapplicable.
PIOTROSKI_EXEMPT_SECTORS = {
    'Financial Services',
    'Financials',
    'ETF',
    'Exchange Traded Fund',
    'Real Estate',
    None,
}

def score_ticker(
    signals_row: dict,
    vix_close: float,
    regime: str | None = None,
    current_date: datetime.date | None = None,
):
    """
    Compute the E1 Ensemble Score (0.0 to 1.0) for a single ticker.
    Includes E1-specific vetoes (V1.4 Safety Hardening).
    """
    # 1. Extract context
    ticker = signals_row.get('ticker')
    sector = signals_row.get('sector')
    b_pct = signals_row.get('breadth_pct', 0.6)
    
    # 2. Compute Votes (Signal Levels -1.0 to +1.0)
    votes = signal_votes.compute_votes(signals_row, breadth_pct=b_pct)
    
    # 3. Aggregate Score based on Regime Weights
    # Inject regime into row so aggregate_score uses the correct weight set
    row_with_context = dict(signals_row)
    if regime:
        row_with_context['regime'] = regime
    
    ensemble_score = signal_votes.aggregate_score(votes, ENSEMBLE_REGIME_WEIGHTS, row=row_with_context)
    
    # 4. Apply E1 Vetoes (Hardening Logic)
    # These vetoes force the score to 0.0 to prevent entry in high-risk setups.
    
    # A. Short Float Veto (>15% shorted)
    short_float = signals_row.get('short_float_pct')
    if short_float is not None and short_float > 0.15:
        if sector not in PIOTROSKI_EXEMPT_SECTORS:
            ensemble_score = 0.0
            
    # B. BEAR Regime Safety Vetoes
    if regime == 'BEAR':
        # Drawdown Veto (Excessive collapse > 65%)
        dd = signals_row.get('drawdown_52w')
        if dd is not None and dd <= config.BEAR_DRAWDOWN_VETO:
            ensemble_score = 0.0
            
        # Quality Veto (Piotroski F-Score floor)
        pio_score = signals_row.get('piotroski_f_score')
        is_pio_exempt = sector in PIOTROSKI_EXEMPT_SECTORS
        if not is_pio_exempt:
            if pio_score is None or pio_score <= config.BEAR_PIOTROSKI_VETO:
                ensemble_score = 0.0
        else:
            # Exempt sectors only veto if pio_score is explicitly low (if available)
            if pio_score is not None and pio_score <= config.BEAR_PIOTROSKI_VETO:
                ensemble_score = 0.0

    # C. Score Decay Veto (Symmetry with e1_trader.py logic)
    # If RSI is extremely overextended, we force AVOID even if ensemble is high.
    rsi = signals_row.get('rsi_14')
    if rsi is not None and rsi > 75:
        ensemble_score = min(ensemble_score, 0.40) # Push below entry thresholds

    # 5. Determine Dominant Cluster (V1.3/V1.4 Attribution)
    weights_regime = regime if regime in ENSEMBLE_REGIME_WEIGHTS else 'SAFE_DEFAULT'
    active_w, _ = ENSEMBLE_REGIME_WEIGHTS.get(weights_regime, ENSEMBLE_REGIME_WEIGHTS.get('SAFE_DEFAULT'))
    dominant_cluster, cluster_dominance_pct = signal_votes.compute_dominant_cluster(votes, active_w)

    return {
        "ticker": ticker,
        "ensemble_score": round(ensemble_score, 4),
        "dominant_cluster": dominant_cluster,
        "cluster_dominance_pct": cluster_dominance_pct,
        "regime": regime,
        "votes": votes
    }
