import math
import logging
import numpy as np
from . import config

logger = logging.getLogger(__name__)

def compute_entry_levels(entry_price: float, atr_14: float, regime: str = 'HEALTHY') -> dict:
    """
    Compute all price levels at entry. These are FIXED for the life of the trade.
    atr_14 is stored as atr_at_entry in the position row and never recomputed.
    """
    stop_mult = config.ATR_STOP_MULTIPLIERS.get(regime, 6.0)
    be_mult = 1.5
    
    # Targets (V1.3/V1.4 Consolidated)
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
    Continuous Conviction Scalar (V1.5 Offensive Lever).
    - 0.72-0.79: 1.0x (Baseline)
    - 0.80-0.89: 1.35x (Offensive)
    - 0.90+:     1.50x (High Conviction)
    """
    if score < 0.72:
        return 0.75
    
    # Linear interpolation between key gates
    scores = [0.72, 0.80, 0.90]
    scalars = [1.00, 1.35, 1.50]
    
    val = np.interp(score, scores, scalars)
    return round(float(val), 4)

def compute_dynamic_sector_cap(sector: str, base_cap: float, sector_rs: float, regime: str) -> float:
    """Computes RS-aware dynamic sector caps."""
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
    adv_20d: float = 0,
    vix_close: float = None,
    hy_spread: float = None,
    skew_compression: float = None,
    sector_cap_pct: float = 0.20,
    cte_mult: float = 1.0  # Pillar 7 CTE Hook
) -> dict:
    """
    Computes position size using the V1.4 Continuous Framework.
    risk_dollars = base_risk * conviction * S10 * CTE
    """
    try:
        if atr_14 is None or atr_14 <= 0:
            return {'skipped': True, 'skip_reason': 'ATR14 unavailable', 'shares': 0, 'dollar_value': 0.0}
        
        # 1. Base Risk (by regime)
        risk_pct = config.RISK_PCT_BY_REGIME.get(regime, 0.010)
        
        base_risk_dollars = portfolio_value * risk_pct
        
        # 2. Conviction Scaling (Continuous)
        conv_mult = conviction_scalar(ensemble_score)
        
        # 3. ATR% Guard
        atr_pct = atr_14 / close_price
        atr_guard_active = False
        if atr_pct > 0.08:
            conv_mult = min(conv_mult, 0.75)
            atr_guard_active = True

        # 4. S10 Macro Multipliers
        s10_scalar = 1.0
        if vix_close is not None and hy_spread is not None:
            s10_scalar = config.get_position_scalar(regime, vix_close, hy_spread, skew_compression)
        
        # 5. Combined Risk Dollars (Hardened Formula)
        risk_dollars = base_risk_dollars * conv_mult * s10_scalar * cte_mult
        
        # 6. Raw Size Calculation
        stop_mult = config.ATR_STOP_MULTIPLIERS.get(regime, 6.0) 
        stop_distance = atr_14 * stop_mult
        shares_raw = risk_dollars / stop_distance
        target_dollar_val = shares_raw * close_price
        
        # 7. The Four-Cap Sequence
        binding_constraint = 'NONE'
        
        portfolio_cap = portfolio_value * config.MAX_POSITION_PCT_E1
        if target_dollar_val > portfolio_cap:
            target_dollar_val = portfolio_cap
            binding_constraint = 'PORTFOLIO_CONCENTRATION'
            
        if target_dollar_val > remaining_sector_budget:
            target_dollar_val = max(0, remaining_sector_budget)
            binding_constraint = 'SECTOR_BUDGET_DYNAMIC' if sector_cap_pct != 0.20 else 'SECTOR_BUDGET_STATIC'
            
        if adv_20d > 0:
            liquidity_cap = (adv_20d * close_price) * 0.005
            if target_dollar_val > liquidity_cap:
                target_dollar_val = liquidity_cap
                binding_constraint = 'LIQUIDITY_ADV'
        
        min_cash_pct = config.MIN_CASH_PCT_BY_REGIME.get(regime, config.E1_CASH_FLOOR)
        min_cash_required = portfolio_value * min_cash_pct
        if cash_available - target_dollar_val < min_cash_required:
            target_dollar_val = max(0, cash_available - min_cash_required)
            binding_constraint = 'CASH_RESERVE'

        dynamic_floor = max(portfolio_value * config.MIN_POSITION_PCT_E1, config.MIN_POSITION_SIZE_E1)
        if target_dollar_val < dynamic_floor:
            return {
                'skipped': True, 
                'skip_reason': f'Below floor', 
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
            'score_scalar': conv_mult,
            'cte_mult': cte_mult,
            'atr_guard_active': atr_guard_active,
            'binding_constraint': binding_constraint,
            'entry_price': close_price,
            'skipped': False,
            'skip_reason': ''
        }

    except Exception as e:
        logger.error(f"Error computing size for {ticker}: {str(e)}")
        return {'skipped': True, 'skip_reason': str(e), 'shares': 0, 'dollar_value': 0.0}
