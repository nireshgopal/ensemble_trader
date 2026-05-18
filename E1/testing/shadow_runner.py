"""
E1 Shadow Mode Runner
=====================
CLI orchestrator that feeds historical dates into the live E1 trader
using a MockAlpacaClient. Runs the full production pipeline (trader +
reconciler + all plumbing) without touching real money or production tables.

Usage:
    # Single day smoke test
    python E1/testing/shadow_runner.py --date 2026-03-15 --verbose

    # Full relay backtest
    python E1/testing/shadow_runner.py --start 2026-01-01 --end 2026-05-01

    # Failure injection stress test
    python E1/testing/shadow_runner.py --date 2026-03-15 --inject oco-failure
    python E1/testing/shadow_runner.py --date 2026-03-15 --inject zero-price-guard
    python E1/testing/shadow_runner.py --date 2026-03-15 --inject staleness-guard

    # Reset sim tables before a clean run
    python E1/testing/shadow_runner.py --start 2026-01-01 --end 2026-05-01 --reset
"""
import sys
import os
import argparse
import logging
import uuid
import json
from datetime import date, datetime, timedelta
from typing import List, Optional
from pathlib import Path

import duckdb
import pandas as pd
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, message="Comparison of Timestamp with datetime.date")

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from E1.core import config, notifier, piotroski
from E1.core.e1_trader import run_e1_trader
from E1.testing.mock_alpaca import MockAlpacaClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('shadow_runner')

# Base project root (assumed to be 2 levels up from E1/testing/shadow_runner.py)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()
DB_PATH = config.DB_PATH

INITIAL_CAPITAL = 50_000.0

# =============================================================================
# CONFIG TABLE OVERRIDE
# Redirects all DB writes from production tables to sim tables.
# =============================================================================

PROD_TO_SIM = {
    'sandbox.e1_positions':          'sandbox.e1_sim_positions',
    'sandbox.e1_position_fills':     'sandbox.e1_sim_position_fills',
    'sandbox.e1_trade_log':          'sandbox.e1_sim_trade_log',
    'sandbox.e1_order_history':      'sandbox.e1_sim_order_history',
    'sandbox.e1_reconciler_flags':   'sandbox.e1_sim_reconciler_flags',
    'sandbox.e1_sector_caps_history':'sandbox.e1_sim_sector_caps_history',
    'sandbox.e1_beta_sweeper_log':   'sandbox.e1_sim_beta_sweeper_log',
}

_original_config = {}

def override_config_tables():
    """Redirect all config table names to sim namespace."""
    global _original_config
    _original_config = {
        'E1_POSITIONS_TABLE':        config.E1_POSITIONS_TABLE,
        'E1_FILLS_TABLE':            config.E1_FILLS_TABLE,
        'E1_TRADE_LOG_TABLE':        config.E1_TRADE_LOG_TABLE,
        'E1_ORDER_HISTORY_TABLE':    config.E1_ORDER_HISTORY_TABLE,
        'E1_RECONCILER_FLAGS_TABLE': config.E1_RECONCILER_FLAGS_TABLE,
    }
    config.E1_POSITIONS_TABLE        = 'sandbox.e1_sim_positions'
    config.E1_FILLS_TABLE            = 'sandbox.e1_sim_position_fills'
    config.E1_TRADE_LOG_TABLE        = 'sandbox.e1_sim_trade_log'
    config.E1_ORDER_HISTORY_TABLE    = 'sandbox.e1_sim_order_history'
    config.E1_RECONCILER_FLAGS_TABLE = 'sandbox.e1_sim_reconciler_flags'
    logger.info("[SHADOW] Config tables redirected to e1_sim_* namespace")

def restore_config_tables():
    """Restore original config table names."""
    for k, v in _original_config.items():
        setattr(config, k, v)

# =============================================================================
# PIOTROSKI POINT-IN-TIME PATCH
# =============================================================================

_original_get_quarterly_pair = piotroski._get_quarterly_pair
_original_extract_yahoo = piotroski._extract_yahoo_financials
_original_get_yahoo_shares = piotroski._get_yahoo_shares

_sim_date_for_piotroski: Optional[date] = None
_FULL_FINANCIALS = {}
_PIOTROSKI_HISTORY_CACHE = {}

def load_financials_cache(con):
    global _FULL_FINANCIALS
    if not _FULL_FINANCIALS:
        logger.info("[SHADOW] Loading financials into global memory cache...")
        df = con.execute("SELECT * FROM refined.financials ORDER BY ticker, report_date DESC").df()
        for ticker, group in df.groupby('ticker'):
            _FULL_FINANCIALS[ticker] = group.to_dict('records')

_YAHOO_CACHE = {}

def prefetch_yahoo_financials(conn):
    """
    PIT-safe pre-extraction of Yahoo financials using SQL-side JSON extraction.
    Much faster than parsing JSON in Python loops.
    """
    global _YAHOO_CACHE
    logger.info("[SHADOW] Pre-extracting PIT-safe Yahoo financials cache (SQL Optimized)...")
    
    # We use json_extract to pull just the raw values directly in DuckDB
    # Only pull the latest 3 snapshots per ticker to keep memory footprint manageable
    query = """
        WITH snapshots AS (
            SELECT 
                ticker, 
                CAST(fetched_at AS DATE) as fetched_at,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.operatingCashflow.raw')::DOUBLE as ocf,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.freeCashflow.raw')::DOUBLE as fcf,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.returnOnAssets.raw')::DOUBLE as roa,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.returnOnEquity.raw')::DOUBLE as roe,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.grossMargins.raw')::DOUBLE as gm,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.currentRatio.raw')::DOUBLE as cr,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.totalDebt.raw')::DOUBLE as td,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.totalRevenue.raw')::DOUBLE as tr,
                json_extract(raw_json, '$.quoteSummary.result[0].financialData.totalCash.raw')::DOUBLE as tc,
                COALESCE(
                    json_extract(raw_json, '$.quoteSummary.result[0].defaultKeyStatistics.sharesOutstanding.raw')::DOUBLE,
                    json_extract(raw_json, '$.quoteSummary.result[0].financialData.sharesOutstanding.raw')::DOUBLE
                ) as so
            FROM yahoo.yahoo_raw
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY fetched_at DESC) <= 3
        )
        SELECT * FROM snapshots ORDER BY ticker, fetched_at DESC
    """
    
    df = conn.execute(query).df()
    
    _YAHOO_CACHE = {}
    for ticker, group in df.groupby('ticker'):
        records = []
        for _, row in group.iterrows():
            records.append({
                'fetched_at': row['fetched_at'],
                'data': {
                    'operatingCashflow': row['ocf'],
                    'freeCashflow':      row['fcf'],
                    'returnOnAssets':     row['roa'],
                    'returnOnEquity':     row['roe'],
                    'grossMargins':      row['gm'],
                    'currentRatio':      row['cr'],
                    'totalDebt':         row['td'],
                    'totalRevenue':      row['tr'],
                    'totalCash':         row['tc'],
                    'sharesOutstanding':  row['so']
                }
            })
        _YAHOO_CACHE[ticker] = records
    logger.info(f"[SHADOW] Cached PIT history for {len(_YAHOO_CACHE)} tickers.")

def prefetch_piotroski_history(conn):
    """
    Loads all pre-computed F-Scores from refined.e1_piotroski_history into memory.
    Bypasses the need for hundreds of thousands of SQL queries during backtests.
    """
    global _PIOTROSKI_HISTORY_CACHE
    logger.info("[SHADOW] Pre-fetching pre-computed Piotroski history cache...")
    
    df = conn.execute("""
        SELECT ticker, score_date, filing_date, f_score_raw, status
        FROM refined.e1_piotroski_history
        ORDER BY ticker, score_date DESC, filing_date DESC
    """).df()
    
    _PIOTROSKI_HISTORY_CACHE = {}
    for ticker, group in df.groupby('ticker'):
        # Store as list of dicts for PIT filtering
        records = []
        for _, row in group.iterrows():
            records.append({
                'score_date': pd.to_datetime(row['score_date']).date(),
                'filing_date': pd.to_datetime(row['filing_date']).date(),
                'f_score': int(row['f_score_raw']),
                'status': row['status']
            })
        _PIOTROSKI_HISTORY_CACHE[ticker] = records
    
    # DIAGNOSTIC
    sample_tkrs = ['AAPL', 'MSFT', 'XOM', 'NVDA']
    for t in sample_tkrs:
        recs = _PIOTROSKI_HISTORY_CACHE.get(t, [])
        if recs:
            logger.info(f"[DIAG] {t} Cache: {len(recs)} records | Latest: {recs[0]['score_date']} | Oldest: {recs[-1]['score_date']}")
        else:
            logger.warning(f"[DIAG] {t} Cache: EMPTY")

    logger.info(f"[SHADOW] Cached {len(df)} pre-computed scores for {len(_PIOTROSKI_HISTORY_CACHE)} tickers.")

def setup_sim_short_float(conn, sim_date: date):
    """Creates a fast temporary table for point-in-time short float."""
    conn.execute("DROP TABLE IF EXISTS sim_latest_short_float")
    conn.execute(f"""
        CREATE TEMPORARY TABLE sim_latest_short_float AS
        SELECT ticker, short_percent_of_float 
        FROM yahoo.analyst_data 
        WHERE fetched_at <= '{sim_date}'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY fetched_at DESC) = 1
    """)

class DuckDBProxy:
    """Wraps DuckDB connection to intercept and patch queries on the fly."""
    def __init__(self, conn, sim_date):
        self._conn = conn
        self.sim_date = sim_date
        
    @property
    def sim_date(self):
        return self._sim_date
        
    @sim_date.setter
    def sim_date(self, value):
        self._sim_date = value
        
    def execute(self, query, parameters=None, **kwargs):
        if isinstance(query, str) and "refined.latest_short_float" in query:
            query = query.replace("refined.latest_short_float", "sim_latest_short_float")
        
        if parameters is not None:
            return self._conn.execute(query, parameters, **kwargs)
        return self._conn.execute(query, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)

def _point_in_time_extract_yahoo_financials(con, ticker, sim_date=None):
    """
    PIT-safe lookup in the Yahoo history cache.
    Finds the latest record that was available ON or BEFORE the simulation date.
    """
    history = _YAHOO_CACHE.get(ticker, [])
    if not history:
        return None
        
    pit_date = sim_date or _sim_date_for_piotroski or date.today()
    for record in history:
        if record['fetched_at'] <= pit_date:
            return record['data']
            
    return None # No data available at this time

def _point_in_time_get_yahoo_shares(con, ticker, sim_date=None):
    """PIT-safe shares lookup from the historical cache."""
    data = _point_in_time_extract_yahoo_financials(con, ticker, sim_date)
    if data:
        return data.get('sharesOutstanding')
    return None

def _point_in_time_quarterly_pair(con, ticker):
    """
    PIT-aware version for shadow mode.
    Filters the global financials cache to only include reports available by _sim_date_for_piotroski.
    """
    all_fins = _FULL_FINANCIALS.get(ticker, [])
    if not all_fins:
        return None, None
        
    # Filter by report_date
    pit_date = _sim_date_for_piotroski or date.today()
    pit_fins = [f for f in all_fins if pd.to_datetime(f['report_date']).date() <= pit_date]
    
    if len(pit_fins) < 2:
        return None, None
        
    # pit_fins is ordered by report_date DESC in the original load_financials_cache query
    return pit_fins[0], pit_fins[1]

def _point_in_time_get_precomputed_fscore(con, ticker, sim_date=None):
    """PIT-safe lookup for pre-computed F-Scores from memory cache."""
    history = _PIOTROSKI_HISTORY_CACHE.get(ticker, [])
    pit_date = sim_date or _sim_date_for_piotroski or date.today()
    for record in history:
        # Convert record date to date object for robust comparison
        r_date = record['score_date']
        if hasattr(r_date, 'date'): r_date = r_date.date()
        elif isinstance(r_date, str): r_date = pd.to_datetime(r_date).date()
        
        if r_date <= pit_date:
            return {
                "f_score": record['f_score'],
                "source": "EDGAR_HISTORY",
                "date": r_date,
                "filing_date": record['filing_date'],
                "status": record['status']
            }
    return None

def _strict_edgar_compute_piotroski(ticker, con, sim_date=None, **kwargs):
    """Enforces EDGAR-only scores for OOS calibration validation."""
    result = piotroski.get_precomputed_fscore(con, ticker, sim_date)
    if result is not None:
        # Update detail to reflect strict mode
        result['detail'] = "Strict Authoritative XBRL"
        result['source'] = "edgar_full"
        return result
    
    # Return a 0-score dictionary to signal a veto without crashing the engine
    return {
        "f_score": 0,
        "source": "STRICT_VETO",
        "detail": "No EDGAR history (Strict Mode)",
        "staleness_days": 999
    }

def set_piotroski_sim_date(sim_date: date, strict=True):
    global _sim_date_for_piotroski
    _sim_date_for_piotroski = sim_date
    # Always use strict EDGAR-only mode for shadow runs to be "humming"
    piotroski.compute_piotroski_live = _strict_edgar_compute_piotroski
    piotroski.get_precomputed_fscore = _point_in_time_get_precomputed_fscore

# =============================================================================
# TELEGRAM PATCH — sends as [SIM] prefix, not silenced
# =============================================================================

def make_sim_telegram(sim_date: date):
    def sim_send(message, parse_mode='HTML'):
        sim_msg = f"🧪 [SIM {sim_date}]\n{message}"
        logger.info(f"Would have sent Telegram: {sim_msg.replace(chr(10), ' | ')}")
        return True
    return sim_send

_original_send_telegram = notifier.send_telegram

def patch_telegram(sim_date: date):
    notifier.send_telegram = make_sim_telegram(sim_date)

def restore_telegram():
    notifier.send_telegram = _original_send_telegram

# =============================================================================
# TRADING DAY CALENDAR
# =============================================================================

def get_trading_days(conn, start: date, end: date) -> List[date]:
    """Returns all trading days in the range from price_history."""
    rows = conn.execute("""
        SELECT DISTINCT date FROM refined.price_history
        WHERE ticker = 'SPY' AND date >= ? AND date <= ?
        ORDER BY date
    """, [start, end]).fetchall()
    return [r[0] for r in rows]

# =============================================================================
# DATA COVERAGE REPORT
# =============================================================================

def print_data_coverage_report(start: date, end: date):
    coverage_flags = {}
    print("\n" + "="*65)
    print("  E1 SHADOW MODE — DATA COVERAGE REPORT")
    print("="*65)
    print(f"  Date Range : {start} to {end}")
    print(f"  Capital    : ${INITIAL_CAPITAL:,.2f}")
    print()
    print(f"  {'Signal':<35} {'Coverage':<20} {'Status'}")
    print(f"  {'-'*60}")

    signals = [
        ("Price / Volume / ATR / RSI",    "Since 2014",        "[OK] FULL"),
        ("Ensemble Scores",               "Since 2014",        "[OK] FULL"),
        ("Market Regime / VIX",           "Since 2014",        "[OK] FULL"),
        ("HY Spread / Macro",             "Since 2014",        "[OK] FULL"),
        ("Financial Stmts (F-Score)",     "Since 2012 (PIT)",  "[OK] POINT-IN-TIME"),
        ("Earnings Calendar",             "Since 2020 (Dolt)", "[OK] FULL"),
        ("Short Float Veto",              "From 2026-03-15",   "[!!] NEUTRAL pre-Mar26"),
    ]

    for name, coverage, status in signals:
        print(f"  {name:<35} {coverage:<20} {status}")
        if "NEUTRAL" in status:
            coverage_flags[name] = "NEUTRALIZED"

    if start < date(2026, 3, 15):
        print()
        print("  [!!] NOTE: Short Float data unavailable before 2026-03-15.")
        print("     Veto will be skipped for those dates -- returns may be")
        print("     slightly overstated in the pre-March period.")

    print("="*65 + "\n")
    return json.dumps(coverage_flags)

# =============================================================================
# EQUITY CURVE SNAPSHOT
# =============================================================================

def record_equity_curve(conn, sim_date: date, mock: MockAlpacaClient,
                        sim_run_id: str, regime: str, coverage_note: str):
    snap = mock.get_portfolio_snapshot()
    conn.execute("""
        INSERT OR REPLACE INTO sandbox.e1_sim_equity_curve
            (sim_run_id, sim_date, portfolio_value, cash, invested,
             open_positions, regime, data_coverage_note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, [sim_run_id, sim_date,
          snap['portfolio_value'], snap['cash'], snap['invested'],
          snap['open_positions'], regime, coverage_note])

# =============================================================================
# RESET SIM TABLES
# =============================================================================

# [REMOVED] reset_sim_tables function was removed to prevent accidental data loss.

# =============================================================================
# FINAL REPORT
# =============================================================================

def generate_report(conn, sim_run_id: str, start: date, end: date):
    print("\n" + "="*65)
    print("  E1 SHADOW MODE — FINAL PERFORMANCE REPORT")
    print("="*65)

    # Trade log
    trades = conn.execute("""
        SELECT ticker, entry_date, exit_date, exit_trigger, pnl_pct, pnl_dollars
        FROM sandbox.e1_sim_positions
        WHERE sim_run_id = ? AND status = 'CLOSED'
        ORDER BY entry_date
    """, [sim_run_id]).df()

    if not trades.empty:
        print(f"\n  {'Ticker':<8} {'Entry':<12} {'Exit':<12} {'Trigger':<25} {'PnL%':>7} {'PnL$':>9}")
        print(f"  {'-'*75}")
        for _, r in trades.iterrows():
            pnl_pct = float(r['pnl_pct'] or 0)
            pnl_dol = float(r['pnl_dollars'] or 0)
            print(f"  {r['ticker']:<8} {str(r['entry_date']):<12} {str(r['exit_date']):<12} "
                  f"{str(r['exit_trigger'] or ''):<25} {pnl_pct*100:>6.2f}% ${pnl_dol:>8.2f}")

    # Summary stats
    equity_curve = conn.execute("""
        SELECT sim_date, portfolio_value FROM sandbox.e1_sim_equity_curve
        WHERE sim_run_id = ? ORDER BY sim_date
    """, [sim_run_id]).df()

    total_trades = len(trades)
    wins = (trades['pnl_pct'] > 0).sum() if not trades.empty else 0
    win_rate = wins / total_trades if total_trades else 0
    final_equity = float(equity_curve['portfolio_value'].iloc[-1]) if not equity_curve.empty else INITIAL_CAPITAL
    total_return = (final_equity / INITIAL_CAPITAL) - 1
    days_range = (end - start).days
    cagr = ((1 + total_return) ** (365 / days_range) - 1) * 100 if days_range > 0 else 0

    # Order plumbing stats
    orders = conn.execute("""
        SELECT side, COUNT(*) as cnt, COUNT(CASE WHEN status='cancelled' THEN 1 END) as cancelled
        FROM sandbox.e1_sim_order_history WHERE sim_run_id = ?
        GROUP BY side
    """, [sim_run_id]).df()

    # Benchmark Comparison
    benchmarks = {}
    for tkr in ['SPY', 'DIA']:
        b_data = conn.execute("""
            SELECT close FROM refined.price_history 
            WHERE ticker = ? AND date <= ?
            ORDER BY date DESC LIMIT 1
        """, [tkr, end]).fetchone()
        b_start = conn.execute("""
            SELECT close FROM refined.price_history 
            WHERE ticker = ? AND date >= ?
            ORDER BY date ASC LIMIT 1
        """, [tkr, start]).fetchone()
        
        if b_data and b_start:
            b_ret = (b_data[0] / b_start[0]) - 1
            b_cagr = ((1 + b_ret) ** (365 / days_range) - 1) * 100 if days_range > 0 else 0
            benchmarks[tkr] = b_cagr

    print(f"\n{'='*65}")
    print(f"  SUMMARY")
    print(f"{'='*65}")
    print(f"  Run ID          : {sim_run_id}")
    print(f"  Period          : {start} to {end}")
    print(f"  Total Trades    : {total_trades}")
    print(f"  Win Rate        : {win_rate*100:.1f}%")
    print(f"  Starting Equity : ${INITIAL_CAPITAL:,.2f}")
    print(f"  Final Equity    : ${final_equity:,.2f}")
    print(f"  Total Return    : {total_return*100:.2f}%")
    print(f"  Annualized CAGR : {cagr:.2f}%")
    for tkr, b_cagr in benchmarks.items():
        diff = cagr - b_cagr
        print(f"  {tkr} CAGR        : {b_cagr:.2f}% ({'+' if diff >= 0 else ''}{diff:.2f}% alpha)")
    if not orders.empty:
        print(f"\n  PLUMBING SUMMARY (Orders):")
        print(orders.to_string(index=False))
    print(f"{'='*65}\n")

    # Update manifest
    conn.execute("""
        UPDATE sandbox.e1_sim_run_manifest
        SET completed_at = CURRENT_TIMESTAMP, final_capital = ?, total_trades = ?,
            win_rate = ?, total_return_pct = ?, cagr = ?
        WHERE sim_run_id = ?
    """, [final_equity, total_trades, win_rate, total_return * 100, cagr, sim_run_id])

# =============================================================================
# MAIN RUNNER
# =============================================================================

def run_shadow(start: date, end: date, inject_scenario: Optional[str] = None,
               reset: bool = False, verbose: bool = False, run_id: Optional[str] = None,
               strict_edgar: bool = False):

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    sim_run_id = run_id if run_id else str(uuid.uuid4())[:12]

    # Isolated local database copy to prevent lock contention with scheduled tasks
    import shutil
    sim_db_dir = PROJECT_ROOT / "scratch"
    sim_db_dir.mkdir(exist_ok=True)
    sim_db_path = sim_db_dir / f"findb_sim_{sim_run_id}.duckdb"
    
    logger.info(f"[SHADOW] Creating isolated local database copy at {sim_db_path}...")
    shutil.copy2(DB_PATH, sim_db_path)
    
    # Override DB_PATH for config and connect to the local copy
    original_db_path = config.DB_PATH
    config.DB_PATH = str(sim_db_path)
    
    conn = duckdb.connect(str(sim_db_path))

    # Ensure sim schema exists in the copy
    schema_sql = os.path.join(os.path.dirname(__file__), 'sim_schema.sql')
    with open(schema_sql, 'r') as f:
        for stmt in f.read().split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception as e:
                    if 'already exists' not in str(e).lower():
                        logger.warning(f"Schema stmt warning: {e}")

    # Override config to point at sim tables
    override_config_tables()

    # Patch Piotroski for point-in-time queries
    # (will be updated each day in the loop)

    # Get trading calendar
    trading_days = get_trading_days(conn, start, end)
    if not trading_days:
        logger.error(f"No trading days found between {start} and {end}")
        return

    logger.info(f"Shadow run {sim_run_id}: {len(trading_days)} trading days | "
                f"inject={inject_scenario or 'none'}")

    # Print data coverage and get flags
    coverage_note = print_data_coverage_report(start, end)

    # Register run in manifest
    conn.execute("""
        INSERT OR REPLACE INTO sandbox.e1_sim_run_manifest
            (sim_run_id, start_date, end_date, initial_capital, inject_scenario, data_coverage_flags)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [sim_run_id, start, end, INITIAL_CAPITAL, inject_scenario, coverage_note])

    # Load financials cache for Piotroski PIT
    load_financials_cache(conn)
    # prefetch_yahoo_financials(conn) # REMOVED per user feedback
    prefetch_piotroski_history(conn)

    # -------------------------------------------------------------------------
    # MAIN DAY LOOP
    # -------------------------------------------------------------------------
    # Build stateful mock client once before the loop
    proxy_conn = DuckDBProxy(conn, trading_days[0])
    mock = MockAlpacaClient(
        conn=proxy_conn,
        sim_date=trading_days[0],
        initial_cash=INITIAL_CAPITAL,
        sim_run_id=sim_run_id,
        inject_scenario=inject_scenario,
    )

    for i, sim_date in enumerate(trading_days):
        logger.info(f"\n{'─'*55}")
        logger.info(f"  [{i+1}/{len(trading_days)}] SHADOW DATE: {sim_date}")
        logger.info(f"{'─'*55}")

        # Advance state to today
        proxy_conn.sim_date = sim_date
        mock.sim_date = sim_date

        # Patch Piotroski with today's sim_date for point-in-time F-Score
        set_piotroski_sim_date(sim_date, strict=True)

        # Patch Telegram with sim prefix
        patch_telegram(sim_date)

        # OPTIMIZATION: Setup temporary short float table for today
        setup_sim_short_float(conn, sim_date)

        # Fetch regime for equity curve snapshot
        regime_row = proxy_conn.execute("""
            SELECT regime FROM refined.market_regime WHERE date = ?
        """, [sim_date]).fetchone()
        regime = regime_row[0] if regime_row else 'UNKNOWN'

        # --- RUN THE FULL REAL TRADER (plumbing + reconciler) ---
        try:
            run_e1_trader(
                simulate=False,       # False = real DB writes (to sim tables)
                manage_only=False,
                _client=mock,
                _conn=proxy_conn,
                _sim_date=sim_date,
            )
        except Exception as e:
            logger.error(f"[SHADOW] Unhandled exception on {sim_date}: {e}", exc_info=True)
            # Continue to next day — do not abort the full run
            conn.execute("""
                INSERT INTO sandbox.e1_sim_reconciler_flags
                    (flag_id, flag_date, flag_type, notes, sim_run_id, sim_date)
                VALUES (?, ?, 'UNHANDLED_EXCEPTION', ?, ?, ?)
            """, [str(uuid.uuid4()), sim_date, str(e), sim_run_id, sim_date])

        # Snapshot equity curve
        record_equity_curve(conn, sim_date, mock, sim_run_id, regime, coverage_note)

    # -------------------------------------------------------------------------
    # POST-RUN CLEANUP & REPORT
    # -------------------------------------------------------------------------
    restore_telegram()
    restore_config_tables()
    piotroski._get_quarterly_pair = _original_get_quarterly_pair
    # piotroski._extract_yahoo_financials = _original_extract_yahoo
    # piotroski._get_yahoo_shares = _original_get_yahoo_shares
    # Note: get_precomputed_fscore didn't have an original patch in this script
    # but we should restore it to the real function if possible.
    from E1.core.piotroski import get_precomputed_fscore as real_get_fscore
    piotroski.get_precomputed_fscore = real_get_fscore

    generate_report(conn, sim_run_id, start, end)
    conn.close()

    # Swiftly export sim reports back to the production database (sub-second write lock)
    logger.info("[SHADOW] Exporting simulation results back to production database...")
    try:
        prod_conn = duckdb.connect(original_db_path)
        
        # Delete existing entries for this run_id to avoid duplication/binder unique errors
        prod_conn.execute("DELETE FROM sandbox.e1_sim_run_manifest WHERE sim_run_id = ?", [sim_run_id])
        prod_conn.execute("DELETE FROM sandbox.e1_sim_equity_curve WHERE sim_run_id = ?", [sim_run_id])
        prod_conn.execute("DELETE FROM sandbox.e1_sim_trade_log WHERE sim_run_id = ?", [sim_run_id])
        prod_conn.execute("DELETE FROM sandbox.e1_sim_positions WHERE sim_run_id = ?", [sim_run_id])
        
        # Temp connection to read from the copied DB again
        temp_conn = duckdb.connect(str(sim_db_path), read_only=True)
        
        # Merge run manifest
        manifest_rows = temp_conn.execute("SELECT * FROM sandbox.e1_sim_run_manifest WHERE sim_run_id = ?", [sim_run_id]).df()
        if not manifest_rows.empty:
            prod_conn.register('temp_manifest', manifest_rows)
            prod_conn.execute("INSERT INTO sandbox.e1_sim_run_manifest SELECT * FROM temp_manifest")
            prod_conn.unregister('temp_manifest')
            
        # Merge equity curve
        equity_rows = temp_conn.execute("SELECT * FROM sandbox.e1_sim_equity_curve WHERE sim_run_id = ?", [sim_run_id]).df()
        if not equity_rows.empty:
            prod_conn.register('temp_equity', equity_rows)
            prod_conn.execute("INSERT INTO sandbox.e1_sim_equity_curve SELECT * FROM temp_equity")
            prod_conn.unregister('temp_equity')
            
        # Merge trade log
        trade_rows = temp_conn.execute("SELECT * FROM sandbox.e1_sim_trade_log WHERE sim_run_id = ?", [sim_run_id]).df()
        if not trade_rows.empty:
            prod_conn.register('temp_trade', trade_rows)
            prod_conn.execute("INSERT INTO sandbox.e1_sim_trade_log SELECT * FROM temp_trade")
            prod_conn.unregister('temp_trade')

        # Merge positions
        pos_rows = temp_conn.execute("SELECT * FROM sandbox.e1_sim_positions WHERE sim_run_id = ?", [sim_run_id]).df()
        if not pos_rows.empty:
            prod_conn.register('temp_pos', pos_rows)
            prod_conn.execute("INSERT INTO sandbox.e1_sim_positions SELECT * FROM temp_pos")
            prod_conn.unregister('temp_pos')
            
        temp_conn.close()
        prod_conn.close()
        logger.info("[SHADOW] Successfully synchronized simulation records back to production.")
    except Exception as e:
        logger.error(f"[SHADOW] Failed to merge simulation results back to production: {e}")
        
    # Cleanup the temporary database file to avoid disk bloat
    try:
        if sim_db_path.exists():
            os.remove(sim_db_path)
            logger.info(f"[SHADOW] Cleaned up temporary database file {sim_db_path}.")
    except Exception as e:
        logger.warning(f"Could not clean up temporary database file: {e}")
        
    # Restore original database path
    config.DB_PATH = original_db_path


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='E1 Shadow Mode Backtester — runs full production pipeline against historical data'
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--date', type=str, help='Single sim date (YYYY-MM-DD)')
    group.add_argument('--start', type=str, help='Start date for range run (YYYY-MM-DD)')

    parser.add_argument('--end', type=str, default=None,
                        help='End date for range run (default: today)')
    parser.add_argument('--inject', type=str, default=None,
                        choices=['oco-failure', 'zero-price-guard', 'staleness-guard'],
                        help='Inject a failure scenario for plumbing stress testing')
    # [REMOVED] --reset flag removed for safety.
    parser.add_argument('--verbose', action='store_true',
                        help='Enable DEBUG-level logging')
    parser.add_argument('--capital', type=float, default=50_000.0,
                        help='Initial capital for simulation (default: $50,000)')
    parser.add_argument('--run-id', type=str, default=None,
                        help='Custom simulation run identifier (e.g. cte_training_v1)')
    parser.add_argument('--strict-edgar', action='store_true',
                        help='Enforce EDGAR-only Piotroski scores (OOS Calibration Mode)')

    args = parser.parse_args()

    # Override global capital if specified
    INITIAL_CAPITAL = args.capital

    if args.date:
        sim_date = date.fromisoformat(args.date)
        run_shadow(start=sim_date, end=sim_date,
                   inject_scenario=args.inject, reset=False, verbose=args.verbose, run_id=args.run_id)
    else:
        start_dt = date.fromisoformat(args.start)
        end_dt = date.fromisoformat(args.end) if args.end else date.today()
        run_shadow(start=start_dt, end=end_dt,
                   inject_scenario=args.inject, reset=False, verbose=args.verbose, 
                   run_id=args.run_id, strict_edgar=args.strict_edgar)
