"""
E1 V1.4 Exit Evaluator — Hardened Production Spec
Architecture:
  1. StopLifecycleManager: 6-8x ATR Safety Stop + Target 2 OCO.
  2. SignalEvaluator: Flat 20-day horizon + 40% Score Decay Veto.
  3. Structured Triggers: Mandatory propagation of exit_trigger for CTE training.
"""
from datetime import date, datetime
import logging
import pandas as pd
from E1.core import config

logger = logging.getLogger(__name__)

DECAY_VETO_THRESHOLDS = {
    # (regime, regime_age_bucket): score_drop_threshold_to_exit
    # Lower number = more tolerant (harder to exit)
    # Higher number = less tolerant (easier to exit)
    ('HEALTHY',  'FRESH'):       0.55,  # Tolerant (Fresh momentum wobbles)
    ('HEALTHY',  'ESTABLISHED'): 0.42,  # Slight loosening
    ('HEALTHY',  'MATURE'):      0.35,  # Tighter (Late-cycle moves are fragile)

    ('FRAGILE',  'FRESH'):       0.40,  
    ('FRAGILE',  'ESTABLISHED'): 0.35,  
    ('FRAGILE',  'MATURE'):      0.28,  

    ('BEAR',     'FRESH'):       0.38,  
    ('BEAR',     'ESTABLISHED'): 0.30,  
    ('BEAR',     'MATURE'):      0.25,  
}

def get_decay_threshold(regime: str, regime_age_days: int) -> float:
    if regime_age_days < 30:
        age_bucket = 'FRESH'
    elif regime_age_days < 90:
        age_bucket = 'ESTABLISHED'
    else:
        age_bucket = 'MATURE'
    return DECAY_VETO_THRESHOLDS.get((regime, age_bucket), 0.40)

def get_trading_days_held(conn, ticker: str, entry_date, as_of_date) -> int:
    """
    Count trading days between entry_date (exclusive) and as_of_date (inclusive)
    using the refined.price_history table as the authoritative trading calendar.
    """
    try:
        result = conn.execute("""
            SELECT COUNT(*) FROM refined.price_history
            WHERE ticker = ?
              AND date > ?
              AND date <= ?
        """, [ticker, entry_date, as_of_date]).fetchone()
        return result[0] if result else 0
    except Exception as e:
        logger.warning(f"Trading day count failed for {ticker}: {e}. Using calendar approximation.")
        cal_days = (as_of_date - entry_date).days if hasattr(entry_date, 'days') else 0
        return int(cal_days * 5 / 7)

def earnings_exit_veto(days_to_earnings: int, days_held: int) -> bool:
    """Force exit if earnings within 2 days and we've held at least 5 days."""
    if days_to_earnings is None:
        return False
    return days_to_earnings <= 2 and days_held >= 5

class StopLifecycleManager:
    """
    V1.4 Stop Lifecycle (Final Specification).
    Simple safety machine: hard stop at regime-specific ATR.
    """
    def evaluate_stop_progression(self, position: dict, current_price: float, 
                                   low_price: float = None, highest_close: float = None) -> dict:
        """
        Stop Lifecycle Engine:
        - Mode 1: INITIAL / STOP_LOSS (Hard ATR-based)
        - Mode 2: TARGET_2 (OCO Sell)
        - Mode 3: BREAKEVEN (Entry + 0.01)
        """
        initial_stop = position.get('initial_stop', 0.0)
        be_trigger = position.get('breakeven_trigger', 0.0)
        stop_stage = position.get('stop_stage', 'INITIAL')
        atr = position.get('atr_at_entry', 0.0)
        
        trigger_price = low_price if low_price is not None else current_price
        
        # 1. BREACH CHECK (Priority 1)
        active_stop = initial_stop if stop_stage == 'INITIAL' else position.get('stop_loss', initial_stop)
        if stop_stage == 'TRAILING':
            active_stop = position.get('trailing_stop', active_stop)
            
        if active_stop > 0 and trigger_price <= active_stop:
            return {
                'action': 'EMERGENCY_MARKET_EXIT',
                'exit_trigger': 'STOP_VIOLATION',
                'exit_price': active_stop,
                'reason': f"{stop_stage} stop {active_stop:.2f} breached."
            }
            
        # 1.5. TARGET 2 CHECK (Simulation OCO mimic)
        target_2 = position.get('target_2', 0.0)
        high_price = highest_close if highest_close is not None else current_price
        if target_2 > 0 and high_price >= target_2:
            return {
                'action': 'SELL',
                'exit_trigger': 'TARGET_2_HIT',
                'exit_price': target_2,
                'reason': f"Target 2 (${target_2:.2f}) reached intraday."
            }
            
        # 2. PROMOTION CHECK (Only if not already trailing/breakeven)
        if stop_stage == 'INITIAL' and be_trigger > 0 and current_price >= be_trigger:
            return {
                'action': 'ADVANCE_TO_BREAKEVEN',
                'new_stop': position['entry_price'] + 0.01,
                'reason': 'Breakeven trigger hit.'
            }
            
        # 3. TRAILING CHECK (V1.6: 1.5x for normal, 4.0x for extended holds)
        if stop_stage in ('BREAKEVEN', 'TRAILING') and highest_close and atr > 0:
            # Extended holds (TRAILING) use 4x ATR, normal holds (BREAKEVEN) use 1.5x
            default_mult = 4.0 if stop_stage == 'TRAILING' else 1.5
            trail_mult = position.get('trailing_mult_override', default_mult)
            
            potential_stop = highest_close - (atr * trail_mult)
            current_stop = position.get('stop_loss', 0.0)
            
            if potential_stop > current_stop:
                return {
                    'action': 'UPDATE_TRAILING_STOP',
                    'new_stop': potential_stop,
                    'reason': f'Trailing stop ratcheted up to ${potential_stop:.2f} ({trail_mult}x ATR)'
                }
                
        return {'action': 'NONE'}

    def is_healthy_bull(self, mdata: dict, current_regime: str) -> bool:
        """V1.6: Check for Healthy Bull sub-state using Day 19 data."""
        # V1.6: Metadata Integrity Check (Hard Fail on missing keys)
        required_keys = ['vix_current', 'spy_price', 'spy_sma50', 'spy_sma200']
        missing = [k for k in required_keys if k not in mdata]
        if missing:
            raise KeyError(f"CRITICAL METADATA MISSING: {missing}. Healthy Bull logic cannot be validated.")

        vix = mdata['vix_current']
        spy_price = mdata['spy_price']
        sma50 = mdata['spy_sma50']
        sma200 = mdata['spy_sma200']
        
        if vix is None or sma50 is None or sma200 is None or spy_price is None:
            return False
            
        return vix <= 18.0 and spy_price > sma50 and spy_price > sma200

    def evaluate_signal_decay(self, position: dict, current_score: float, today: date, current_regime: str = None, yesterday_regime: str = None, conn=None, mdata: dict = None) -> dict:
        """
        Signal-Driven Exits (Config D + Config B Veto):
        1. Primary: Flat 20-day time exit (V1.6: Conditional Extension to 35d).
        2. Veto: 40% score decay from entry score (if held > 5 days).
        """
        entry_date = self._parse_entry_date(position)
        if entry_date is None:
            return {'action': 'NONE'}
            
        ticker = position.get('ticker')
        if conn and ticker:
            days_held = get_trading_days_held(conn, ticker, entry_date, today)
        else:
            cal_days = (today - entry_date).days
            days_held = int(cal_days * 5 / 7)
        
        # 1. Almanac Exit Veto (Priority check before extension)
        days_to_earn = position.get('days_to_earnings')
        if earnings_exit_veto(days_to_earnings=days_to_earn, days_held=days_held):
            return {
                'action': 'CLOSE_POSITION',
                'exit_trigger': 'ALMANAC_EXIT',
                'reason': f'Earnings in {days_to_earn}d',
                'urgency': 'EOD_LIMIT'
            }

        # 2. Time Exit / Extension Gate (V1.6)
        if days_held >= 20:
            if days_held >= 35:
                return {
                    'action': 'CLOSE_POSITION',
                    'exit_trigger': 'TIME_EXIT_EXT',
                    'reason': f'Max extended hold of {days_held} trading days reached',
                    'urgency': 'EOD_LIMIT'
                }
            
            # Extension Check
            if days_held >= 20 and position.get('status') == 'OPEN':
                # V1.6: Healthy Bull Extension Gate
                entry_score = position.get('entry_score', 0.0)
                is_healthy = self.is_healthy_bull(mdata, current_regime)
                has_min_conviction = entry_score >= 0.80
                has_min_score = (current_score or 0) >= 0.72
                
                # Calculate PnL in ATR units
                current_pnl_atr = 0.0
                if position.get('entry_price', 0) > 0 and position.get('atr_at_entry', 0) > 0:
                    current_pnl_dollars = mdata.get('current_price', 0) - position['entry_price']
                    current_pnl_atr = current_pnl_dollars / position['atr_at_entry']
                
                has_min_pnl = current_pnl_atr >= 2.0  # T1 Profitability
                eligible = is_healthy and has_min_conviction and has_min_score and has_min_pnl
                
                if eligible:
                    # V1.6: Trigger Extension
                    trail_mult = 2.5
                    highest_close = max(position.get('highest_close', 0), mdata.get('current_price', 0))
                    potential_stop = highest_close - (position.get('atr_at_entry', 0) * trail_mult)
                    current_stop = position.get('stop_loss', 0)
                    
                    if potential_stop > current_stop:
                        return {
                            'action': 'UPDATE_TRAILING_STOP',
                            'new_stop': potential_stop,
                            'new_trail_mult': trail_mult,
                            'reason': f'Healthy Bull extension ratcheting (Stop: {potential_stop:.2f})'
                        }
                        
                    return {
                        'action': 'EXTEND_HOLD',
                        'new_trail_mult': trail_mult,
                        'reason': f'Healthy Bull extension triggered at day {days_held} (Score: {current_score:.2f}, PnL: {current_pnl_atr:.1f}x ATR)'
                    }
                else:
                    return {
                        'action': 'CLOSE_POSITION',
                        'exit_trigger': 'TIME_EXIT_20D',
                        'reason': f'Held {days_held} trading days (Not eligible for extension)',
                        'urgency': 'EOD_LIMIT'
                    }
            
            # If already extended (TRAILING), check if we should still hold
            if position.get('stop_stage') == 'TRAILING':
                if not self.is_healthy_bull(mdata, current_regime):
                    return {
                        'action': 'CLOSE_POSITION',
                        'exit_trigger': 'REGIME_EXIT',
                        'reason': f'Market exited Healthy Bull state during extension at day {days_held}',
                        'urgency': 'EOD_LIMIT'
                    }
                else:
                    return {
                        'action': 'HOLD',
                        'reason': f'Continuing extended hold in Healthy Bull state (Day {days_held})'
                    }

        # 3. Score Decay Veto
        entry_score = position.get('entry_score', 0.65)
        decay_baseline = position.get('score_at_entry_baseline') or entry_score
        
        if days_held > 5 and current_score is not None:
            regime_transitioned = (yesterday_regime is not None and current_regime != yesterday_regime)
            if regime_transitioned:
                new_baseline = current_score
                pid = position.get('id')
                if conn and pid is not None and not pd.isna(pid):
                    conn.execute(f"""
                        UPDATE {config.E1_POSITIONS_TABLE}
                        SET ensemble_score = ?,
                            score_at_entry_baseline = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, [new_baseline, new_baseline, int(pid)])
                logger.info(
                    f"{ticker} REGIME TRANSITION {yesterday_regime}→{current_regime}: "
                    f"Decay baseline reset to {new_baseline:.3f} (was {decay_baseline:.3f})."
                )
                return {'action': 'HOLD', 'reason': f'REGIME_TRANSITION_BASELINE_RESET_{current_regime}'}
            else:
                regime_age_days = mdata.get('regime_age_days', 0) if mdata else 0
                threshold = get_decay_threshold(current_regime or 'HEALTHY', regime_age_days)
                
                # Check if core technical signals are still intact
                sig_price_stage = mdata.get('sig_price_stage') if mdata else None
                sig_rs_12month = mdata.get('sig_rs_12month') if mdata else None
                
                # S_C: sig_price_stage == 1.0 (Minervini Stage 2 setup)
                # S_A: sig_rs_12month > 0
                core_signals_intact = (
                    sig_price_stage == 1.0 and 
                    sig_rs_12month is not None and sig_rs_12month > 0
                )
                
                # If core signals intact, widen threshold by 5% (more tolerant, harder to exit)
                effective_threshold = threshold + (0.05 if core_signals_intact else 0.0)
                
                if current_score < (decay_baseline * (1.0 - effective_threshold)):
                    return {
                        'action': 'CLOSE_POSITION',
                        'exit_trigger': 'SCORE_DECAY_VETO',
                        'reason': f'Score {current_score:.2f} < {(1.0 - effective_threshold)*100:.0f}% of baseline {decay_baseline:.2f} (Threshold: {effective_threshold:.2f}, Core Intact: {core_signals_intact})',
                        'urgency': 'EOD_LIMIT'
                    }
            
        return {'action': 'NONE'}

    def _parse_entry_date(self, position: dict):
        entry_date = position.get('entry_date')
        if entry_date is None: return None
        if hasattr(entry_date, 'date'): return entry_date.date()
        if isinstance(entry_date, str):
            try:
                return datetime.strptime(entry_date, '%Y-%m-%d').date()
            except ValueError:
                return datetime.strptime(entry_date.split(' ')[0], '%Y-%m-%d').date()
        return entry_date

def evaluate(position: dict, mdata: dict, current_regime: str = 'HEALTHY', yesterday_regime: str = 'HEALTHY', today: date = None, conn=None) -> dict:
    """
    Unified entry point. mdata: current_score, current_price, high_price, highest_close, low_price
    """
    slm = StopLifecycleManager()
    low_price = mdata.get('low_price', mdata.get('current_price'))
    highest_close = mdata.get('highest_close', mdata.get('current_price'))
    
    # 1. Stop / T2
    stop_res = slm.evaluate_stop_progression(position, mdata['current_price'], 
                                            low_price=low_price, highest_close=highest_close)
    
    if stop_res['action'] in ('EMERGENCY_MARKET_EXIT', 'SELL'):
        return {
            'action': 'SELL',
            'exit_trigger': stop_res.get('exit_trigger', 'STOP_VIOLATION'),
            'reason': stop_res['reason'],
            'exit_price': stop_res.get('exit_price'),
            'urgency': stop_res.get('urgency', 'EMERGENCY_MARKET_EXIT')
        }
    
    if stop_res['action'] in ('ADVANCE_TO_BREAKEVEN', 'UPDATE_TRAILING_STOP'):
        return stop_res # Propagate update actions directly
        
    # 2. Time / Decay
    eval_today = today if today is not None else date.today()
    decay_res = slm.evaluate_signal_decay(position, mdata.get('current_score'), eval_today, 
                                         current_regime=current_regime, yesterday_regime=yesterday_regime, conn=conn, mdata=mdata)
    
    if decay_res['action'] == 'CLOSE_POSITION':
        return {
            'action': 'SELL',
            'exit_trigger': decay_res['exit_trigger'],
            'reason': decay_res['reason'],
            'exit_price': mdata.get('current_price'),
            'urgency': decay_res.get('urgency', 'EOD_LIMIT')
        }
    
    if decay_res['action'] == 'EXTEND_HOLD':
        return decay_res # Propagate extension action directly to trader
    
    return decay_res if decay_res['action'] == 'HOLD' else {'action': 'HOLD', 'reason': 'Thesis Intact'}
