import math
import logging
import numpy as np
from . import config

logger = logging.getLogger(__name__)


def compute_entry_levels(entry_price: float, atr_14: float, dominant_cluster: str, regime: str = 'HEALTHY') -> dict:
    """
    Compute all price levels at entry. These are FIXED for the life of the trade.
    atr_14 is stored as atr_at_entry in the position row and never recomputed.
    
    # Final Flash V1.3/V1.4 Specification: Safety stop at Regime-Gated ATR
    """
    stop_mult = config.ATR_STOP_MULTIPLIERS.get(regime, 6.0)
    be_mult = 1.5
    
    # Targets (V1.3 Consolidated)
    t1_mult = config.ATR_TARGET_1
    t2_mult = config.ATR_TARGET_2
    
    initial_stop      = round(entry_price - (atr_14 * stop_mult), 4)
    breakeven_trigger  = round(entry_price + (atr_14 * be_mult), 4)
    t1_target          = round(entry_price + (atr_14 * t1_mult), 4)
    t2_target          = round(entry_price + (atr_14 * t2_mult), 4)
    
    return {
        'initial_stop':      initial_stop,
        'breakeven_trigger': breakeven_trigger,
        't1_target':         t1_target,
        't2_target':         t2_target,
        'stop_mult_used':    stop_mult,
        'atr_at_entry':      atr_14,
    }

def conviction_scalar(score: float) -> float:
    """
    Continuous Conviction Scalar (V1.3 Rule 2.2).
    Linearly interpolates between known IC-weighted thresholds.
    
    0.60 -> 0.75
    0.70 -> 0.92 (approx 0.9167)
    0.80 -> 1.08 (approx 1.0833)
    0.90+ -> 1.25 (Cap)
    """
    if score < 0.60:
        return 0.75  # Minimum scalar for any qualifying trade
    
    # Define interpolation points
    scores = [0.60, 0.70, 0.80, 0.90]
    scalars = [0.75, 0.9167, 1.0833, 1.25]
    
    # Linearly interpolate
    val = np.interp(score, scores, scalars)
    return round(float(val), 4)

def compute_dynamic_sector_cap(sector: str, base_cap: float, sector_rs: float, regime: str) -> float:
    """
    Computes RS-aware dynamic sector caps (Phase 2).
    Only active in HEALTHY or EUPHORIA regimes.
    
    Rules:
    - If sector_rs >= 1.15: cap = min(base_cap * 2.0, 0.40)
    - If sector_rs <= 0.85: cap = base_cap * 0.5
    - Otherwise: cap = base_cap
    """
    if regime not in ['HEALTHY', 'EUPHORIA']:
        return base_cap
        
    if sector_rs >= 1.15:
        return min(base_cap * 2.0, 0.40)
    elif sector_rs <= 0.85:
        return base_cap * 0.5
        
    return base_cap

def compute_position_size(
    ticker: str,
    ensemble_score: float,
    close_price: float,
    atr_14: float,
    regime: str,
    portfolio_value: float,
    cash_available: float,
    open_positions: list,
    sector: str,
    remaining_sector_budget: float,
    adv_20d: float = 0, # Average Daily Volume (shares)
    vix_close: float = None,
    hy_spread: float = None,
    skew_compression: float = None,
    sector_cap_pct: float = 0.20 # Default V1.3 cap
) -> dict:
    """
    Computes position size using the V1.3 Continuous Framework.
    Applies the four-cap sequence + ATR volatility guard.
    """
    try:
        if atr_14 is None or atr_14 <= 0:
            return {'skipped': True, 'skip_reason': 'ATR14 unavailable', 'shares': 0, 'dollar_value': 0.0}
        
        # 1. Base Risk (1.5% of equity per trade in HEALTHY)
        risk_pct = {
            'HEALTHY': 0.015,
            'FRAGILE': 0.010,
            'BEAR':    0.0075,
        }.get(regime, 0.010)
        
        base_risk_dollars = portfolio_value * risk_pct
        
        # 2. Conviction Scaling (Continuous)
        conviction_mult = conviction_scalar(ensemble_score)
        
        # 3. ATR% Guard (Grenade Filter)
        # If ATR% > 8%, we cap conviction at 0.75x to reduce exposure to hyper-volatility
        atr_pct = atr_14 / close_price
        atr_guard_active = False
        if atr_pct > 0.08:
            conviction_mult = min(conviction_mult, 0.75)
            atr_guard_active = True

        # Apply Conviction + S10 Macro Multipliers
        s10_scalar = 1.0
        if vix_close is not None and hy_spread is not None:
            s10_scalar = config.get_position_scalar(regime, vix_close, hy_spread, skew_compression)
        
        risk_dollars = base_risk_dollars * conviction_mult * s10_scalar
        
        # 4. Raw Size Calculation
        # Number of shares such that [Regime] * ATR move equals risk_dollars
        stop_mult = config.ATR_STOP_MULTIPLIERS.get(regime, 6.0) 
        stop_distance = atr_14 * stop_mult
        shares_raw = risk_dollars / stop_distance
        target_dollar_val = shares_raw * close_price
        
        # 5. The Four-Cap Sequence (V1.3 Rule 2.2)
        binding_constraint = 'NONE'
        
        # Cap 1: Portfolio Concentration (10% max per E1 position)
        portfolio_cap = portfolio_value * config.MAX_POSITION_PCT_E1
        if target_dollar_val > portfolio_cap:
            target_dollar_val = portfolio_cap
            binding_constraint = 'PORTFOLIO_CONCENTRATION'
            
        # Cap 2: Sector Budget (Respecting Dynamic RS Scaling)
        if target_dollar_val > remaining_sector_budget:
            target_dollar_val = max(0, remaining_sector_budget)
            binding_constraint = 'SECTOR_BUDGET_DYNAMIC' if sector_cap_pct != 0.20 else 'SECTOR_BUDGET_STATIC'
            
        # Cap 3: Liquidity Guard (0.5% of 20d ADV)
        if adv_20d > 0:
            liquidity_cap = (adv_20d * close_price) * 0.005
            if target_dollar_val > liquidity_cap:
                target_dollar_val = liquidity_cap
                binding_constraint = 'LIQUIDITY_ADV'
        
        # Cash Guard (Final hard check against available funds)
        min_cash_pct = config.MIN_CASH_PCT_BY_REGIME.get(regime, config.E1_CASH_FLOOR)
        min_cash_required = portfolio_value * min_cash_pct
        if cash_available - target_dollar_val < min_cash_required:
            target_dollar_val = max(0, cash_available - min_cash_required)
            binding_constraint = 'CASH_RESERVE'

        # Cap 4: Dynamic Minimum Floor (scaled to equity)
        # We use a 5% floor (e.g. $500 on $10k, $2,500 on $50k)
        dynamic_floor = max(portfolio_value * config.MIN_POSITION_PCT_E1, config.MIN_POSITION_SIZE_E1)
        if target_dollar_val < dynamic_floor:
            return {
                'skipped': True, 
                'skip_reason': f'Below ${dynamic_floor:,.0f} floor ({config.MIN_POSITION_PCT_E1:.0%} of equity)', 
                'shares': 0, 
                'dollar_value': 0.0,
                'binding_constraint': 'MIN_SIZE_FLOOR'
            }

        final_shares = math.floor(target_dollar_val / close_price)
        if final_shares <= 0:
            return {'skipped': True, 'skip_reason': 'Shares = 0 after caps', 'shares': 0, 'dollar_value': 0.0}
            
        return {
            'shares': final_shares,
            'dollar_value': final_shares * close_price,
            'stop_loss': close_price - stop_distance,
            'target_1': close_price + (config.ATR_TARGET_1 * atr_14),
            'target_2': close_price + (config.ATR_TARGET_2 * atr_14),
            'score_scalar': conviction_mult,
            'atr_guard_active': atr_guard_active,
            'binding_constraint': binding_constraint,
            'entry_price': close_price,
            'skipped': False,
            'skip_reason': ''
        }

    except Exception as e:
        logger.error(f"Error computing size for {ticker}: {str(e)}")
        return {'skipped': True, 'skip_reason': str(e), 'shares': 0, 'dollar_value': 0.0}
