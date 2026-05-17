# ==============================================================================
# SECTION 1: SHARED FOUNDATION (GLOBAL)
# These settings affect both Strategy V1-V3 and Strategy E1.
# ==============================================================================

import os
from pathlib import Path

# Base project root (assumed to be 2 levels up from E1/core/config.py)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()
DB_PATH = r"C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb"

MIN_AVG_VOLUME = 500_000      # > 500K shares avg daily floor

# -- Risk & Position Sizing Defaults (Global) --
ATR_STOP_MULT = 2.0           # 2x ATR initial stop
ATR_TARGET_1 = 2.0            # Partial exit at +2x ATR
ATR_TARGET_2 = 4.0            # Full exit at +4x ATR
MAX_SECTOR_PCT = 0.25         # 25% max sector exposure
SLIPPAGE_BPS = 5              # 5 basis points assumed
TIME_STOP_DAYS = 20           # Max holding period without gain

# -- Regime-aware Limits (Transitioning to Sector Budgets) --
MAX_POSITIONS_BY_REGIME = {'HEALTHY': 10, 'FRAGILE': 6, 'BEAR': 4} # Legacy reference
MIN_CASH_PCT_BY_REGIME = {'HEALTHY': 0.15, 'FRAGILE': 0.30, 'BEAR': 0.45}
ENTRY_SCORE_THRESHOLD = {'HEALTHY': 0.72, 'FRAGILE': 0.68, 'BEAR': 0.80}
RISK_PCT_BY_REGIME = {'HEALTHY': 0.015, 'FRAGILE': 0.005, 'BEAR': 0.0025}
DAILY_NEW_ENTRY_CAP = {'HEALTHY': 2, 'FRAGILE': 1, 'BEAR': 1}

# -- Legacy Fallbacks (V3 Compatibility) --
MAX_POSITIONS = 10            # Default for V3
MAX_POSITION_PCT = 0.15       # 15% of capital
MIN_CASH_PCT = 0.15           # 15% cash reserve
ENABLE_CAPITAL_RECYCLING = False
RISK_PCT = 0.015              # 1.5% risk per trade (V3 default)

# ==============================================================================
# SECTION 2: STRATEGY V3 (LEGACY/CONCURRENT)
# The 100-point manual scoring system. Used by daily_scanner.py / scorer.py.
# ==============================================================================

# ── Scoring Thresholds (Section 3 — V3 Optimized, 100-point scale) ───────────

# 3.1 Swing Value: Drawdown from 52W High -> Continuous Scaling
DRAWDOWN_MAX_PTS = 15
DRAWDOWN_CAP_PCT = -0.3150
DRAWDOWN_FLOOR_PCT = -0.0528

# PE vs Sector Median (DEPRECATED for Swing Value in V3 - moved to Fundamental)

# 3.2 Relative Strength: 3-Month RS vs SPY -> Continuous Scaling
RS_3M_MAX_PTS = 16
RS_3M_CAP_PCT = 0.1231
RS_3M_FLOOR_PCT = -0.0250

# 3.2 Relative Strength: 10-Day RS vs SPY -> Continuous Scaling
RS_10D_MAX_PTS = 20
RS_10D_CAP_PCT = 0.0461
RS_10D_FLOOR_PCT = -0.0217

# 3.3 Technical Setup: RSI 14 -> V3 Continuous Scaling
RSI_MAX_PTS = 10
RSI_CAP = 30.0       # deep oversold = max pts
RSI_FLOOR = 65.0     # neutral = 0 pts
# Legacy bucket scoring (kept for V1/V2 compatibility)
RSI_THRESHOLDS = [30, 40, 50, 60]
RSI_POINTS     = [10,  7,  4,  2, 0]

# 3.3 Technical Setup: Volume Spike (FROZEN — proven noise in V3)
VOLUME_THRESHOLDS = [2.0, 1.5]
VOLUME_POINTS     = [  2,   1, 0]

# 3.4 Trend Quality -> V3 Optimized
TREND_ABOVE_SMA200 = 15
TREND_ABOVE_SMA50 = 9
TREND_NEAR_SMA200 = 5

# 3.5 Fundamental Quality (V3)
FUND_DEBT_MAX_PTS = 2
FUND_DEBT_CAP = 0.0123       # debt/assets ratio: lower = more pts
FUND_DEBT_FLOOR = 0.5908
FUND_MARGIN_MAX_PTS = 4
FUND_MARGIN_CAP = 0.1759     # net margin: higher = more pts
FUND_MARGIN_FLOOR = -0.0702
FUND_PE_DISC_MAX_PTS = 6
FUND_PE_DISC_CAP = 0.4326    # sector pe discount: higher = more pts
FUND_PE_DISC_FLOOR = -0.1470

# 3.6 Event Timing: 12 pts base (10 pts original → rescaled)
EVENT_MAX_PTS = 12

# ── Score Tiers (V3 — now percentages of 100-point scale) ───────────────────

TIER_STRONG_BUY = 72
TIER_BUY = 44
TIER_QUALIFIED = 38

# Regime-aware TIER_BUY override (default = no override, same as TIER_BUY)
# In FRAGILE regime, this threshold replaces TIER_BUY for tier assignment.
TIER_BUY_FRAGILE_OVERRIDE = TIER_BUY
# ── FRAGILE Regime Gate (V3 NEW) ────────────────────────────────────────────
FRAGILE_GATE_ENABLED = False   # Trade in FRAGILE (Balanced standard)

# ── VIX Bonus (V3 Optimized) ───────────────────────────────────────────────
VIX_BONUS_THRESHOLD = 21
VIX_BONUS_PTS = 3
VIX_BONUS_SCORE_FLOOR = 54

# ── Sector Momentum (Section 3.5) ───────────────────────────────────────────

SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Cyclical": "XLY",      # Alias used by some data sources
    "Consumer Staples": "XLP",
    "Consumer Defensive": "XLP",     # Alias
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}
SECTOR_MOMENTUM_THRESHOLD = 0.02   # +/-2% threshold for sector RS vs SPY

# ── EDGAR & Quality Gates ──────────────────────────────────────────────────
EDGAR_S3ASR_EXEMPT_MARKET_CAP = 10_000_000_000

# ── BEAR Regime Filters (provisional — pending attribution validation) ──
BEAR_DRAWDOWN_VETO = -0.65       # Drawdown ≤ -65% → hard veto in BEAR
BEAR_PIOTROSKI_VETO = 3          # §10: veto if F-score <= 3.

# ── Industry Profile Overrides (Section 3.9) ───────────────────────────

# Industries requiring a minimum tier for entry (prevents shake-outs)
INDUSTRY_TIER_FLOORS = {
    "Luxury Goods": "STRONG_BUY"
}

# Empirical penalties for structurally underperforming industries
INDUSTRY_SOFT_PENALTIES = {
    "Health Information Services": -8
}

# Industries under observation (non-scoring tag)
INDUSTRY_OBSERVATION_MODE = [
    "Leisure"
]

# ── V4 Feature Defaults (all disabled / no effect for V3 configs) ────────────
V4_BETA_PENALTY_THRESHOLD = 99.0   # disabled
V4_BETA_PENALTY_PTS = 0
V4_FCF_POSITIVE_BONUS = 0
V4_FCF_NEGATIVE_PENALTY = 0
V4_SECTOR_CORR_THRESHOLD = 99.0    # disabled
V4_SECTOR_CORR_PENALTY = 0
V4_VOL30D_PENALTY_THRESHOLD = 99.0 # disabled
V4_VOL30D_PENALTY_PTS = 0
V4_OCF_POSITIVE_BONUS = 0
V4_OCF_NEGATIVE_PENALTY = 0

# ==============================================================================
# SECTION 3: STRATEGY E1 (MODERN ENSEMBLE)
# The dynamic weighting system. Uses signal_weights.json for scoring.
# ==============================================================================

SIGNAL_WEIGHTS_PATH = "docs/signal_weights_CANDIDATE.json"
WEIGHTS_MODE = "frozen"        # GOVERNANCE: 'frozen' or 'experimental'

MAX_POSITION_PCT_E1 = 0.15    # 15% max single position (E1 standard)
E1_CASH_FLOOR = 0.15          # Core cash floor for E1 engine
ENABLE_BETA_SWEEPER = False   # Master flag for L3 Beta Sweeper (Experimental)
ATR_STOP_MULTIPLIERS = {
    'HEALTHY': 6.0,
    'FRAGILE': 7.0,
    'BEAR': 8.0
}
ATR_TARGET_1 = 2.0
ATR_TARGET_2 = 4.0

# ── Sandbox Tables (V1.3 Parallel Build) ──────────────────────────────────
# This ensures E1/ code only trades in the 'sandbox' schema.
# Inputs (Signals/Regime) are SHARED, Outputs (Positions/Fills) are ISOLATED.
E1_POSITIONS_TABLE = "sandbox.e1_positions"
E1_FILLS_TABLE = "sandbox.e1_position_fills"
E1_RECONCILER_FLAGS_TABLE = "sandbox.e1_reconciler_flags"
E1_ATTRIBUTION_TABLE = "sandbox.e1_signal_attribution"
E1_ORDER_HISTORY_TABLE = "sandbox.e1_order_history"
E1_TRADE_LOG_TABLE = "sandbox.e1_trade_log"
E1_DECAY_TRACKING_TABLE = "sandbox.e1_decay_exit_tracking"

# These remain in 'refined' for shared read-access across both systems
E1_SCORES_TABLE = "refined.ensemble_daily_scores"
MARKET_REGIME_TABLE = "refined.market_regime"
IC_HISTORY_TABLE = "refined.ic_history"

# ── Pillar 7 (Contextual Training Engine) ─────────────────────────────────
cte_mult_active = False
CTE_LOOKUP_TABLE = "sandbox.e1_cte_lookup"

# Note: E1 primary logic is in signal_votes.py and e1_trader.py
USE_NEW_S10_GATE = False      # SAFETY FLAG: If False, S10 sizer logic is Shadow Only.

# ── Budgeting 2.0: Sector Allocation Framework ──────────────────────────────
# These percentages are governance-locked. 
# Changes require the same formal process as signal weight updates.
E1_SECTOR_BUDGETS = {
    'Technology': 0.20,
    'Financial Services': 0.15,
    'Healthcare': 0.12,
    'Consumer Discretionary': 0.12,
    'Consumer Staples': 0.08,
    'Energy': 0.10,
    'Industrials': 0.10,
    'Basic Materials': 0.08,
    'Communication Services': 0.08,
    'Utilities': 0.05,
    'Real Estate': 0.05,
    'Other': 0.10,
    'Miscellaneous': 0.10,
}

# Standardize messy provider strings to our canonical budget sectors
SECTOR_STRING_NORMALIZATION = {
    'Consumer Discretionary': 'Consumer Discretionary',
    'Consumer Cyclical': 'Consumer Discretionary',
    'Consumer Defensive': 'Consumer Staples',
    'Consumer Staples': 'Consumer Staples',
    'Financials': 'Financial Services',
    'Financial Services': 'Financial Services',
    'Information Technology': 'Technology',
    'Technology': 'Technology',
    'Communications': 'Communication Services',
    'Communication Services': 'Communication Services',
    'Basic Materials': 'Basic Materials',
    'Industrials': 'Industrials',
    'Energy': 'Energy',
    'Utilities': 'Utilities',
    'Real Estate': 'Real Estate',
    'Healthcare': 'Healthcare',
}

# Explicit sector overrides for genuinely ambiguous conglomerates or total provider failures.
# Note: Try to use SECTOR_STRING_NORMALIZATION for global naming issues first.
MANUAL_SECTOR_OVERRIDES = {
    'GEV': 'Industrials',
    'BRK.B': 'Financial Services',
    'HAL': 'Energy',
    'DOW': 'Basic Materials'
}

MIN_POSITION_PCT_E1 = 0.05    # 5% of equity minimum floor per position
MIN_POSITION_SIZE_E1 = 500    # Hard absolute floor (never go below $500 even on tiny accounts)
MAX_STOCKS_PER_SECTOR = 3     # Count-cap backstop to prevent fragmentation

def get_position_scalar(regime, vix_close, hy_spread, skew_compression=None):
    """
    S10 Dynamic Sizing Logic (Step 2 replacement for binary VIX gate).
    
    Parameters
    ----------
    skew_compression : float, optional
        Default is 0.0 for dates before 2019-02-09 (spy_vol_skew data unavailable).
        This means the skew_compression branch never fires pre-2019.
        The VIX + HY spread logic remains fully active for the full 2014-2026 window.
    """
    # skew_compression defaults to 0.0 for dates before 2019-02-09
    sc = skew_compression or 0.0

    # 1. The ONE real danger condition (Hard Skip)
    if hy_spread > 5.5 and regime == 'FRAGILE':
        return 0.0   # Feb 2020 territory
    
    # 2. Reduce but don't skip in genuine stress
    if hy_spread > 4.5 and regime == 'BEAR':
        return 0.5   # half size
    
    # 3. The asymmetric opportunity buckets — go harder
    if vix_close > 30 and hy_spread < 5.5:
        return 1.25  # 25% oversize — best historical return bucket
    
    if sc < -0.02 and vix_close > 20:
        return 1.15  # skew compression signal active
    
    # Default — normal
    return 1.0

# ── VIX Regime (Section 4 & 6) ──────────────────────────────────────────────

VIX_REGIMES = {
    "calm":    {"lo": 0,  "hi": 15,  "size_mult": 0.8, "max_trades_week": 1},
    "normal":  {"lo": 15, "hi": 25,  "size_mult": 1.0, "max_trades_week": 2},
    "fear":    {"lo": 25, "hi": 35,  "size_mult": 1.2, "max_trades_week": 3},
    "extreme": {"lo": 35, "hi": 9999,"size_mult": 1.0, "max_trades_week": 1},
}

def get_vix_regime(vix_close: float) -> dict:
    """Return the VIX regime dict for the given VIX level."""
    for name, regime in VIX_REGIMES.items():
        if regime["lo"] <= vix_close < regime["hi"]:
            return {**regime, "name": name}
    return {**VIX_REGIMES["normal"], "name": "normal"}

# ── Slippage Multiplier (Section 4) ─────────────────────────────────────────

def get_slippage_mult(vix_close: float) -> float:
    """Widen ATR stops by 20% in fear markets."""
    return 1.2 if vix_close > 25 else 1.0

# ── Breadth Gate (Section 6) ────────────────────────────────────────────────

# SPY < SMA200: Hard block (no new longs except Deep Value Reversal)
# SPY < SMA50 by > 2%: Reduce position size by 50%, allow BUY+ tiers
# SPY < SMA50 by ≤ 2%: No restriction (proximity buffer)
BREADTH_SMA50_PROXIMITY = 0.02   # 2% buffer — ignore minor dips below SMA50
BREADTH_SMA50_SIZE_MULT = 0.50   # Half position size when SPY below SMA50

# ── Loss Limits / Circuit Breakers (Section 7) ──────────────────────────────

DAILY_LOSS_LIMIT = -0.04      # -4% → close all, pause 1 day
WEEKLY_LOSS_LIMIT = -0.08     # -8% → halve sizes next week
MONTHLY_LOSS_LIMIT = -0.15    # -15% → stop trading entirely

# ── Universe Qualification (Section 2) ──────────────────────────────────────

MIN_ROE = 0.08                # ROE > 8%
MIN_ANALYST_COVERAGE = 5      # >= 5 analysts
MIN_AVG_VOLUME = 500_000      # > 500K shares avg daily

# ── Execution Assumptions ───────────────────────────────────────────────────

ENTRY_DELAY_DAYS = 1          # Signal today → enter tomorrow at open
# DB_PATH removed (Redundant, using global definition at top)
USE_PROXY_EARNINGS = False    # Use live Yahoo earnings calendar instead of proxy dates

# ── Dual-Playbook Configurations ────────────────────────────────────────────

CONFIGS = {
    "v3_optimized": {},  # V3 is the new baseline (module-level vars above)
    
    "v3_balanced": {
        "TIER_STRONG_BUY": 72,       # Lower bar for high-conviction
        "TIER_BUY": 44,              # Aligned with §3.10
        "FRAGILE_GATE_ENABLED": False, # Trade in FRAGILE with 50% size (set via BREADTH_SMA50_SIZE_MULT)
        "MAX_POSITIONS": 15,         # Increase capacity
        "RISK_PCT": 0.02,            # Increase risk per trade from 1.5% to 2%
    },

    "V3_SB_Only_Defensive": {
        "TIER_STRONG_BUY": 72,
        "TIER_BUY": 44,
        "FRAGILE_GATE_ENABLED": False,
        "MAX_POSITIONS": 15,
        "RISK_PCT": 0.02,
        # Regime-aware: raise BUY threshold to STRONG_BUY level in FRAGILE
        # Note: BEAR regime is already blocked by scorer gate (line 508-512)
        "TIER_BUY_FRAGILE_OVERRIDE": 72,
    },

    "trial_V3_march26": {
        "TIER_STRONG_BUY": 75,
        "TIER_BUY": 44,
        "TIER_QUALIFIED": 40,
        "MAX_POSITIONS": 15,
        "SENTIMENT_PTS_POS": 3,
        "SENTIMENT_PTS_NEG": -3,
        "SECTOR_MOM_PTS": 5,
        "SOFT_GATE_PENALTY": -10,
    },

    "v3_opportunistic": {
        "TIER_STRONG_BUY": 68,       # Aggressive bar
        "TIER_BUY": 35,              # Capture more momentum
        "FRAGILE_GATE_ENABLED": False,
        "MAX_POSITIONS": 20,         # Broaden capture
        "RISK_PCT": 0.03,            # 3% risk/trade for higher compounding
        "MIN_CASH_PCT": 0.05,        # Minimal cash drag
        "BREADTH_SMA50_SIZE_MULT": 0.75, # Lean in harder during fragile markets
    },

    "trial_0383": {     # RETIRED — V1/V2 era, kept for reference
        "DRAWDOWN_MAX_PTS":    11,
        "DRAWDOWN_CAP_PCT":   -0.32,
        "DRAWDOWN_FLOOR_PCT": -0.05,
        "RS_3M_MAX_PTS":       19,
        "RS_3M_CAP_PCT":       0.10,
        "RS_3M_FLOOR_PCT":    -0.05,
        "RS_10D_MAX_PTS":      20,
        "RS_10D_CAP_PCT":      0.05,
        "RS_10D_FLOOR_PCT":   -0.03,
        "RSI_POINTS":         [15, 11, 6, 3, 0],
        "TIER_STRONG_BUY":     75,
        "TIER_BUY":            50,
        "TIER_QUALIFIED":      35,
    },

    "V4_Balanced": {
        # Inherit V3 base settings
        "TIER_STRONG_BUY": 72,
        "TIER_BUY": 44,
        "FRAGILE_GATE_ENABLED": False,
        "MAX_POSITIONS": 15,
        "RISK_PCT": 0.02,
        # V4 features enabled (thresholds from spec examples - Phase 2 showed all r < 0.05)
        "V4_BETA_PENALTY_THRESHOLD": 1.5,    # Phase 2 r=0.002
        "V4_BETA_PENALTY_PTS": -5,
        "V4_FCF_POSITIVE_BONUS": 1,
        "V4_FCF_NEGATIVE_PENALTY": -2,
        "V4_SECTOR_CORR_THRESHOLD": 0.75,    # No historical data to validate
        "V4_SECTOR_CORR_PENALTY": -3,
        "V4_VOL30D_PENALTY_THRESHOLD": 0.6,  # Phase 2 r=0.026
        "V4_VOL30D_PENALTY_PTS": -3,
        "V4_OCF_POSITIVE_BONUS": 1,
        "V4_OCF_NEGATIVE_PENALTY": -2,
    },
}

# Store the original "v2_defaults" so we can revert if needed
import sys
_current_module = sys.modules[__name__]
_BASE_VARS = {k: v for k, v in vars(_current_module).items() if k.isupper() and not k.startswith("_") and k != "CONFIGS"}

def load_config(name="trial_0383"):
    """Load a specific configuration, overwriting module variables."""
    if name not in CONFIGS:
        raise KeyError(
            f"Config '{name}' not found. "
            f"Available: {list(CONFIGS.keys())}"
        )

    cfg = CONFIGS[name]

    # Reset to baseline first
    for k, v in _BASE_VARS.items():
        setattr(_current_module, k, v)

    # Apply overrides
    for key, value in cfg.items():
        if hasattr(_current_module, key):
            setattr(_current_module, key, value)
    return cfg

# Auto-set to V3_SB_Only_Defensive config on import
load_config("V3_SB_Only_Defensive")
