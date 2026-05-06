import os
import sys
import math
import logging
from datetime import date, datetime
import uuid
import duckdb
import pandas as pd
import dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, GetOrdersRequest, StopOrderRequest, TakeProfitRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderType, QueryOrderStatus
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
import argparse

# Load environment variables (API Keys)
dotenv.load_dotenv()

# Ensure the root of the project is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# We use absolute imports for the Sandbox build (E1 package)
from E1.core import exit_evaluator, e1_sizer, signal_votes, config, notifier, piotroski
from E1.core.signal_votes import load_regime_weights
from E1.core.e1_reconciler import run_e1_reconciler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# DB_PATH removed, using config.DB_PATH for absolute path safety
IC_SUMMARY_PATH = 'docs/ic_summary.csv'
WEIGHTS_JSON_PATH = 'docs/signal_weights.json'

# We use signal_votes.compute_dominant_cluster now

def safe_date(d):
    """Ensure we have a datetime.date object from either a date, string, or Timestamp."""
    import pandas as pd
    from datetime import datetime, date
    if isinstance(d, str):
        return datetime.strptime(d, '%Y-%m-%d').date()
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, datetime):
        return d.date()
    return d

def initialize_e1_client():
    e1_key = os.getenv('E1_ALPACA_KEY')
    e1_secret = os.getenv('E1_ALPACA_SECRET')
    
    assert e1_key, "E1_ALPACA_KEY not set — refusing to start"
    assert e1_secret, "E1_ALPACA_SECRET not set — refusing to start"
    
    client = TradingClient(api_key=e1_key, secret_key=e1_secret, paper=True)
    data_client = StockHistoricalDataClient(api_key=e1_key, secret_key=e1_secret)
    account = client.get_account()
    logger.info(f"Initialized Alpaca Trader & Data Client for Account: {account.account_number} (Status: {account.status})")
    return client, data_client

def fetch_portfolio_state(client):
    account = client.get_account()
    portfolio_value = float(account.portfolio_value)
    cash_available = float(account.cash)
    return portfolio_value, cash_available

def gap_up_veto(prev_close: float, live_quote: float) -> bool:
    """V1.3 Rule: No entry if stock gapped up > 4% (Staleness Guard)."""
    if not prev_close or not live_quote:
        return False
    gap_pct = (live_quote - prev_close) / prev_close
    return gap_pct > 0.04

def _heal_protection(pos, client, open_orders, market_data_lookup=None, simulate=False):
    """
    Ensures an open position has its required Stop-Loss and Profit Targets.
    If protection is missing, it cancels all orphaned orders for the ticker 
    and re-submits a clean safety net to avoid "insufficient quantity" locks.
    If the stock is already below the stop price, it triggers an emergency liquidation.
    """
    ticker = pos['ticker']
    shares = pos['shares']
    target_1_hit = pos.get('target_1_hit', False)
    # Handle pandas NA/None to avoid TypeError: boolean value of NA is ambiguous
    if pd.isna(target_1_hit):
        target_1_hit = False
    
    # Filter orders for this ticker
    ticker_orders = [o for o in open_orders if o.symbol == ticker]
    existing_stops = [o for o in ticker_orders if o.type in [OrderType.STOP, OrderType.STOP_LIMIT, 'stop', 'stop_limit']]
    existing_limits = [o for o in ticker_orders if o.type in [OrderType.LIMIT, 'limit']]
    
    # Determine if healing is needed
    # V1.3 Note: We focus on the core safety net (Stop and Target 2) for healing.
    needs_stop = not existing_stops
    t2_val = pos.get('target_2')
    needs_t2_limit = False
    if t2_val and not pd.isna(t2_val):
        needs_t2_limit = not any(round(float(o.limit_price), 2) == round(float(t2_val), 2) for o in existing_limits if o.limit_price)
    else:
        logger.warning(f"  [WARN] {ticker}: target_2 is missing. Skipping T2 heal.")
    
    if not needs_stop and not needs_t2_limit:
        return None # Position is sufficiently protected for v1.3
        
    logger.info(f"  [HEALING] {ticker}: Protection gap detected. Reconstructing safety net...")
    
    if not simulate:
        try:
            import time
            # 1. Clear ANY existing orphaned orders for this ticker to unlock quantity
            if ticker_orders:
                for o in ticker_orders:
                    try:
                        client.cancel_order_by_id(o.id)
                    except: pass
                # Small sleep to allow Alpaca to release the qty lock
                if not hasattr(client, '_sim_date'):
                    time.sleep(1.0)
            
            # --- INVENTORY GUARD: confirm Alpaca still holds shares ──────────
            try:
                alpaca_resp = client.get_open_position(ticker)
                real_alpaca_qty = int(float(alpaca_resp.qty))
            except Exception:
                real_alpaca_qty = 0
                
            if real_alpaca_qty <= 0:
                logger.critical(f"ABORT HEAL: {ticker} — Alpaca reports {real_alpaca_qty} shares. Double-sell prevented.")
                return "ABORTED"
                
            if real_alpaca_qty != shares:
                logger.warning(f"SHARES MISMATCH: {ticker} DB={shares}, Alpaca={real_alpaca_qty}. Using Alpaca qty.")
                shares = real_alpaca_qty

            # 2. Logic: Submit simple Consolidated Bracket Order (Target 2 + Stop)
            t2_p = pos.get('target_2')
            stop_p = pos.get('stop_loss')

            if t2_p and stop_p and not math.isnan(t2_p) and not math.isnan(stop_p):
                try:
                    order_data = LimitOrderRequest(
                            symbol=ticker,
                            qty=shares,
                            side=OrderSide.SELL,
                            limit_price=round(t2_p, 2), # Primary limit for OCO
                            time_in_force=TimeInForce.GTC,
                            order_class=OrderClass.OCO,
                            take_profit=TakeProfitRequest(limit_price=round(t2_p, 2)),
                            stop_loss=StopLossRequest(stop_price=round(stop_p, 2))
                        )
                    logger.info(f"  [DIAGNOSTIC] Submitting {ticker} OCO: {order_data}")
                    client.submit_order(order_data)
                    logger.info(f"  [OK] Consolidated OCO: Target2 @ ${t2_p:.2f} | Stop @ ${stop_p:.2f}")
                except Exception as e:
                    # Emergency Exit only if stop is explicitly violated
                    err_str = str(e).lower()
                    is_stop_violation = False
                    
                    if "42210000" in err_str: # Insufficient buying power / validation error
                        # Fallback mathematically
                        try:
                            q_resp = client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=ticker))
                            current = float(q_resp[ticker].ask_price)
                            if current < float(stop_p):
                                is_stop_violation = True
                        except Exception:
                            # Re-fetch failed, use string matching as last resort
                            if "stop_price must be less than current_price" in err_str or "price below stop" in err_str:
                                is_stop_violation = True
                    elif "stop_price must be less than current_price" in err_str:
                        is_stop_violation = True
                        
                    if is_stop_violation:
                        logger.warning(f"  [EMERGENCY] {ticker} violation: price below stop! Liquidating.")
                        client.submit_order(MarketOrderRequest(symbol=ticker, qty=shares, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                        return "EMERGENCY_EXIT"
                    else:
                        logger.error(f"  [HEAL ERROR] {ticker}: API validation failed ({e}). Manual review required.")
                        return "FAILED"
            else:
                logger.warning(f"  [SKIP] {ticker}: Cannot heal due to missing targets/stops in DB.")

            return "HEALED"

                
        except Exception as e:
            logger.error(f"  [HEALING FAILED] {ticker} Sync: {e}")
            return "FAILED"
    else:
        # Simulation Logic
        stop_val = pos['stop_loss'] if pos['stop_loss'] and not math.isnan(pos['stop_loss']) else "CALCULATED"
        logger.info(f"  [SIMULATE] Would cancel {len(ticker_orders)} orders and restore full bracket for {ticker} (Stop: {stop_val})")
        return "SIMULATED"

def get_sector_rs_lookup(conn, as_of_date=None):
    """
    Fetches pre-calculated 120-day trailing Relative Strength for all sectors vs SPY.
    """
    if as_of_date is None:
        as_of_date = date.today()
    
    date_str = as_of_date.strftime('%Y-%m-%d')
    
    try:
        df_rs = conn.execute(f"SELECT sector, rs_value FROM refined.historical_sector_rs WHERE as_of_date = '{date_str}'").df()
        if len(df_rs) > 0:
            return dict(zip(df_rs['sector'], df_rs['rs_value']))
        else:
            logger.warning(f"No pre-calculated Sector RS found for {date_str}. Falling back to 1.0.")
            return {}
    except Exception as e:
        logger.error(f"Failed to fetch Sector RS: {e}. Falling back to 1.0.")
        return {}

def execute_beta_sweeper(client, conn, portfolio_value, cash_available, regime, simulate=False, as_of_date=None):
    """
    Task 2: Beta Sweeper.
    Deploys idle cash into SPY if in HEALTHY/EUPHORIA near market close.
    Unwinds position if regime turns BEAR/FRAGILE.
    
    EXPERIMENTAL: Currently disabled by config.ENABLE_BETA_SWEEPER due to 
    crash-integrity failure in 2020/2022 ladder validation.
    """
    if not config.ENABLE_BETA_SWEEPER:
        return

    BULL_REGIMES = ['HEALTHY', 'EUPHORIA']
    SYMBOL = 'SPY'
    
    if as_of_date is None:
        as_of_date = date.today()
    today_str = as_of_date.isoformat()
    
    # 1. Unwind logic (Exit Beta Sweep if BEAR/FRAGILE)
    if regime not in BULL_REGIMES:
        db_sweep = conn.execute(f"SELECT id, ticker, shares FROM {config.E1_POSITIONS_TABLE} WHERE is_beta_sweep = TRUE AND status = 'OPEN'").fetchall()
        for pos_id, ticker, shares in db_sweep:
            logger.warning(f"  [BETA SWEEPER] Regime Shift ({regime}) detected. Unwinding {ticker} sweep.")
            if not simulate:
                try:
                    client.submit_order(MarketOrderRequest(symbol=ticker, qty=shares, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                    conn.execute(f"UPDATE {config.E1_POSITIONS_TABLE} SET status = 'CLOSED', exit_date = '{today_str}', exit_trigger = 'REGIME_SHIFT_SWEEP_UNWIND' WHERE id = {pos_id}")
                except Exception as e: logger.error(f"Failed sweep unwind: {e}")
        return

    # 2. Deployment logic (HEALTHY/EUPHORIA)
    exposure = (portfolio_value - cash_available) / portfolio_value
    if exposure < 0.85 and cash_available > (portfolio_value * 0.15):
        # Check if already have a sweep position to avoid duplication (Idempotency)
        existing = conn.execute(f"SELECT id FROM {config.E1_POSITIONS_TABLE} WHERE is_beta_sweep = TRUE AND status = 'OPEN'").fetchone()
        if existing:
            logger.info("  [BETA SWEEPER] Sweep position already exists. Idempotency skip.")
            return

        target_exposure = 0.95
        sweep_dollars = portfolio_value * (target_exposure - exposure)
        
        logger.info(f"  [BETA SWEEPER] Low Exposure ({exposure:.1%}) detected in {regime}. Sweeping ${sweep_dollars:,.2f} into {SYMBOL}.")
        
        if not simulate:
            try:
                # Fetch live price for sizing
                q_resp = client.get_latest_quote(SYMBOL) # Mock-safe or data client
                price = float(q_resp.ask_price) if q_resp.ask_price > 0 else 0
                if price == 0: return # Skip if price unavailable
                
                shares = int(sweep_dollars / price)
                if shares > 0:
                    client.submit_order(MarketOrderRequest(symbol=SYMBOL, qty=shares, side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                    # Record as a position
                    conn.execute(f"""
                        INSERT INTO {config.E1_POSITIONS_TABLE} (ticker, shares, entry_price, entry_date, status, regime_at_entry, is_beta_sweep)
                        VALUES ('{SYMBOL}', {shares}, {price}, '{today_str}', 'OPEN', '{regime}', TRUE)
                    """)
                    conn.execute(f"""
                        INSERT INTO sandbox.e1_beta_sweeper_log (date, regime, portfolio_value, cash_at_trigger, exposure_pre, sweep_amt, symbol, action)
                        VALUES ('{today_str}', '{regime}', {portfolio_value}, {cash_available}, {exposure}, {sweep_dollars}, '{SYMBOL}', 'BUY')
                    """)
            except Exception as e: logger.error(f"Beta sweep failed: {e}")
        else:
            logger.info(f"  [SIMULATE] Would sweep approx ${sweep_dollars:,.2f} into {SYMBOL}")

def run_e1_trader(simulate=False, manage_only=False, _client=None, _conn=None, _sim_date=None):
    if manage_only:
        logger.info(f"Starting Strategy E1 Morning Audit (Exits Only)... (SIMULATE: {simulate})")
    else:
        logger.info(f"Starting Strategy E1 Trader run... (SIMULATE: {simulate})")
    
    # ── PHASE 2: DB SCHEMA UPDATES ──
    conn_init = duckdb.connect(config.DB_PATH)
    try:
        # PRAGMA check to avoid Dependency Errors on redundant ALTERS
        cols = [c[1] for c in conn_init.execute("PRAGMA table_info('sandbox.e1_positions')").fetchall()]
        
        if 'is_beta_sweep' not in cols:
            conn_init.execute("ALTER TABLE sandbox.e1_positions ADD COLUMN is_beta_sweep BOOLEAN DEFAULT FALSE")
        if 'score_at_entry_baseline' not in cols:
            conn_init.execute("ALTER TABLE sandbox.e1_positions ADD COLUMN score_at_entry_baseline FLOAT")
        if 'sector_rs_at_entry' not in cols:
            conn_init.execute("ALTER TABLE sandbox.e1_positions ADD COLUMN sector_rs_at_entry FLOAT")
        if 'effective_sector_cap' not in cols:
            conn_init.execute("ALTER TABLE sandbox.e1_positions ADD COLUMN effective_sector_cap FLOAT")
        
        conn_init.execute('''
        CREATE TABLE IF NOT EXISTS sandbox.e1_sector_caps_history (
            date DATE, regime VARCHAR, sector VARCHAR, base_cap FLOAT, sector_rs FLOAT, effective_cap FLOAT, adjustment_reason VARCHAR, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn_init.execute('''
        CREATE TABLE IF NOT EXISTS sandbox.e1_beta_sweeper_log (
            date DATE, regime VARCHAR, portfolio_value FLOAT, cash_at_trigger FLOAT, exposure_pre FLOAT, sweep_amt FLOAT, symbol VARCHAR, action VARCHAR, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
    except Exception as e:
        logger.warning(f"Schema update notice: {e}")
    finally:
        conn_init.close()

    # 1-3. Init client & fetch account state
    # Shadow Mode: accept injected mock client; fall back to real Alpaca in production
    if _client is not None:
        client = _client
        data_client = _client  # MockAlpacaClient implements both interfaces
        logger.info("[SHADOW MODE] Using injected MockAlpacaClient — no real Alpaca calls")
    else:
        client, data_client = initialize_e1_client()

    portfolio_value, cash_available = fetch_portfolio_state(client)
    
    # --- FIX 1: Fetch actual Alpaca positions once at startup as a guardrail ---
    try:
        alpaca_positions = {p.symbol: int(float(p.qty)) 
                            for p in client.get_all_positions()
                            if float(p.qty) > 0}  # long positions only
    except Exception as e:
        logger.error(f"Cannot fetch Alpaca positions: {e}. Aborting.")
        return
    
    logger.info(f"E1 Portfolio Value: ${portfolio_value:,.2f} | Cash: ${cash_available:,.2f}")
    
    # Shadow Mode: accept injected connection; fall back to new real connection in production
    conn = _conn if _conn is not None else duckdb.connect(config.DB_PATH, read_only=simulate)
    # --- STRATEGY CONTEXT & TEMPORAL ISOLATION ---
    effective_date = _sim_date or date.today()
    today_str = effective_date.isoformat()
    
    # Shadow Mode: extract sim_run_id if provided via client
    sim_run_id = getattr(_client, 'sim_run_id', None) if _client else None
    
    # Freshness check (Re-enabled after emergency recovery)
    # Shadow Mode: check that the closest available signal date matches sim_date
    # Production Mode: require that signal data is from today
    if not manage_only:
        max_signals_date = conn.execute(f"SELECT MAX(date) FROM {config.E1_SCORES_TABLE}").fetchone()[0]
        if _sim_date:
            # In shadow mode, query signals for sim_date specifically
            sim_signals_date = conn.execute(
                f"SELECT MAX(date) FROM {config.E1_SCORES_TABLE} WHERE date <= ?", [_sim_date]
            ).fetchone()[0]
            if sim_signals_date is None or str(sim_signals_date) != today_str:
                logger.error(f"STALENESS SHUTDOWN: no signals for sim_date {today_str} (closest: {sim_signals_date}).")
                if _conn is None:
                    conn.close()
                return
        else:
            if str(max_signals_date) != today_str:
                if os.getenv('E1_IGNORE_STALENESS') == '1':
                    logger.warning(f"  [TEST MODE] Ignoring staleness: signals {max_signals_date} vs today {today_str}")
                else:
                    logger.error(f"STALENESS SHUTDOWN: signals data is {max_signals_date}, today is {today_str}. Refusing to trade on stale data.")
                    conn.close()
                    return
        
        # Multi-run support enabled.
    
    # --- REAL-TIME SYNC HARDENING: Sync Database to Alpaca Truth ---
    logger.info("Performing Real-Time Alpaca-DB synchronization...")
    try:
        db_open_rows = conn.execute(f"SELECT id, ticker, shares FROM {config.E1_POSITIONS_TABLE} WHERE status = 'OPEN'").fetchall()
        for pos_id, ticker, db_shares in db_open_rows:
            real_shares = alpaca_positions.get(ticker, 0)
            
            if real_shares == 0:
                logger.warning(f"  [SYNC] {ticker}: Found in DB but MISSING in Alpaca. Marking as CLOSED.")
                if not simulate:
                    conn.execute(f"""
                        UPDATE {config.E1_POSITIONS_TABLE} 
                        SET status = 'CLOSED', exit_date = '{today_str}', 
                            exit_trigger = 'ALPACA_SYNC_DESYNC', 
                            updated_at = CURRENT_TIMESTAMP 
                        WHERE id = {pos_id}
                    """)
                    conn.execute(f"""
                        INSERT INTO {config.E1_TRADE_LOG_TABLE} (position_id, ticker, action, trade_date, trigger, regime)
                        VALUES ({pos_id}, '{ticker}', 'EXIT', '{today_str}', 'ALPACA_SYNC_DESYNC', 'UNKNOWN')
                    """)
            elif real_shares != db_shares:
                logger.warning(f"  [SYNC] {ticker}: Quantity mismatch (DB={db_shares}, Alpaca={real_shares}). Updating DB.")
                if not simulate:
                    conn.execute(f"UPDATE {config.E1_POSITIONS_TABLE} SET shares = {real_shares}, updated_at = CURRENT_TIMESTAMP WHERE id = {pos_id}")
                    
        logger.info("Alpaca-DB synchronization complete.")
    except Exception as e:
        logger.error(f"Failed during Alpaca-DB synchronization: {e}")

    # 4. Initialize DataFrames (portfolio state written AFTER trading completes)
    signals_df = pd.DataFrame()
    candidates = pd.DataFrame()

    # 5. Load today's signals & market regime
    macro_query = f"""
        WITH skew_latest AS (
            SELECT 
                AVG(skew_25d) OVER (ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as skew_5d,
                AVG(skew_25d) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as skew_20d
            FROM refined.spy_vol_skew
            WHERE date <= ?
            ORDER BY date DESC LIMIT 1
        )
        SELECT 
            r.date,
            r.regime,
            r.vix_close, 
            m.hy_spread,
            (SELECT COALESCE(skew_5d - skew_20d, 0.0) FROM skew_latest) as skew_compression
        FROM {config.MARKET_REGIME_TABLE} r
        JOIN refined.macro_daily m ON r.date = m.date
        WHERE r.date <= ?
        ORDER BY r.date DESC LIMIT 2
    """
    macro_rows = conn.execute(macro_query, [today_str, today_str]).fetchall()
    
    if not macro_rows:
        logger.error("FATAL: Could not fetch Macro/Regime context. Aborting.")
        if _conn is None:
            conn.close()
        return

    macro_row = macro_rows[0]
    macro_date = macro_row[0]
    current_regime = macro_row[1]
    yesterday_regime = macro_rows[1][1] if len(macro_rows) > 1 else None
    vix_close = macro_row[2]
    hy_spread = macro_row[3]
    skew_compression = macro_row[4]

    # Safe logging for macro values (handle None)
    vix_str = f"{vix_close:.2f}" if vix_close is not None else "N/A"
    hy_str = f"{hy_spread:.2f}" if hy_spread is not None else "N/A"
    skew_str = f"{skew_compression:.4f}" if skew_compression is not None else "N/A"
    
    logger.info(f"S10 CONTEXT: Date={macro_date} | Regime={current_regime} (Prev: {yesterday_regime}) | VIX={vix_str} | HY={hy_str} | SkewComp={skew_str}")
    
    # Debug: Threshold identification
    entry_threshold = config.ENTRY_SCORE_THRESHOLD.get(current_regime, 0.65)
    logger.info(f"REGIME MONITOR: current_regime={current_regime} -> entry_threshold={entry_threshold}")

    # CREDIT ALERT MONITOR (S10 Rule)
    # Staleness Check: FRED data has a lag, but if > 3 business days, don't trust the Veto
    hy_data_age = (effective_date - macro_date).days
    credit_veto_active = True
    stale_hy_data = False
    
    if hy_data_age > 3:
        logger.warning(f"S10 HY Spread data is stale ({hy_data_age} days old). Using last known value.")
        stale_hy_data = True

    s10_scalar = 1.0
    if stale_hy_data and hy_spread > 3.5:
        logger.warning(f"S10 UNCERTAINTY PREMIUM: Stale data + elevated HY ({hy_spread:.2f}). Capping s10_scalar at 0.75")
        s10_scalar = min(s10_scalar, 0.75)
        
    if vix_close > 35:
        logger.warning(f"S10 VIX CIRCUIT BREAKER: VIX > 35 ({vix_close:.2f}). Capping s10_scalar at 0.50")
        s10_scalar = min(s10_scalar, 0.50)

    if credit_veto_active and hy_spread > 5.5:
        logger.critical(f"S10 CREDIT DANGER: HY Spread {hy_spread:.2f} > 5.5. ENTRY VETO ACTIVE.")
        if current_regime == 'FRAGILE':
            logger.critical("S10 DANGER + FRAGILE: Hard exit from scanner.")
            manage_only = True # Automatically downshift to management-only mode
    elif credit_veto_active and hy_spread >= 4.0:
        logger.warning(f"S10 CREDIT WATCH: HY Spread {hy_spread:.2f} is elevating. Exercise caution.")

    # Load signals with required veto columns (explicit selection to avoid ambiguity)
    query = f"""
        SELECT 
            s.ticker, s.date, s.close_price, s.volume, s.atr_14, s.rsi_14, 
            s.drawdown_52w, s.sector, s.industry, s.is_sp500, s.breadth_pct,
            e.ensemble_score,
            e.sig_ma_crossover,
            e.sig_rs_3month,
            e.sig_sector_momentum,
            e.sig_ma_slope,
            e.sig_rsi_oversold,
            e.sig_fundamental,
            a.short_percent_of_float,
            s.vol_20d_avg as volume,
            COALESCE(ph.high, s.close_price) as high
        FROM refined.daily_signals_ml s
        LEFT JOIN {config.E1_SCORES_TABLE} e ON s.ticker = e.ticker AND s.date = e.date
        LEFT JOIN refined.latest_short_float a ON s.ticker = a.ticker
        LEFT JOIN refined.price_history ph ON s.ticker = ph.ticker AND s.date = ph.date
        WHERE s.date = '{today_str}'
          AND e.ensemble_score IS NOT NULL
          AND s.ticker NOT IN ('SPY', 'QQQ', 'IWM', 'DIA', 'VXX', '$VIX')
          AND s.close_price >= 1.0
        ORDER BY e.ensemble_score DESC, s.ticker ASC
    """
    signals_df = conn.execute(query).df()
    print(f"DEBUG: signals_df length = {len(signals_df)}")
    
    market_data_lookup = {row['ticker']: row.to_dict() for _, row in signals_df.iterrows()}
    
    # ── PHASE 2: Sector RS Computation (Task 1) ──
    # Computes 3-month trailing RS vs SPY for all sectors to drive dynamic budgeting.
    sector_rs_lookup = get_sector_rs_lookup(conn, as_of_date=(_sim_date or date.today()))
    
    # 5. Load Market Data & Strategy Version
    # Staleness guard: refuse to trade if weights are > 7 days old
    _weights_version = "unknown"
    try:
        import json as _json
        _weights_raw = _json.load(open(WEIGHTS_JSON_PATH))
        _metadata = _weights_raw.get("_metadata", {})
        _gen_at = _metadata.get("generated_at", "").split(" ")[0] # Just the date
        _weights_version = _metadata.get("version", "unknown")
        
        if _gen_at:
            from datetime import datetime as _dt
            _age_days = (effective_date - _dt.strptime(_gen_at, "%Y-%m-%d").date()).days
            if _age_days > 7:
                logger.error(f"FATAL: signal_weights.json is {_age_days} days old. Run recompute_weights.py first.")
                if _conn is None:
                    conn.close()
                return
            logger.info(f"Strategy Version: {_weights_version} (Published: {_gen_at})")
    except Exception as e:
        logger.warning(f"Could not check strategy version/staleness: {e}")

    
    # 6. Load current open E1 positions
    # 6. LOAD SYNCHRONIZED POSITIONS
    open_positions_df = conn.execute(f"SELECT * FROM {config.E1_POSITIONS_TABLE} WHERE status = 'OPEN'").df()
    open_positions = [row.to_dict() for _, row in open_positions_df.iterrows()]
    
    # 7.1 Fetch existing open orders once to avoid per-ticker API calls
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        all_open_orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        print(f"DEBUG: Found {len(all_open_orders)} open orders in Alpaca.")
    except Exception as e:
        logger.warning(f"Failed to fetch open orders for reconciliation: {e}")
        all_open_orders = []
        print(f"DEBUG: Failed to fetch open orders: {e}")

    closed_count = 0
    audit_results = [] # Collect for Telegram summary
    # 7. EXIT LOOP
    for pos in open_positions:
        try:
            ticker = pos['ticker']
            mdata = market_data_lookup.get(ticker)
            
            if not mdata:
                # V1.4: Fallback to price_history if ticker is missing from daily signals (F-20 fix)
                logger.debug(f"Ticker {ticker} not in daily signals. Fetching fallback market data from price_history...")
                fallback_res = conn.execute(f"""
                    SELECT close as close_price, high as high_price, low as low_price, volume
                    FROM refined.price_history 
                    WHERE ticker = '{ticker}' AND date = '{today_str}'
                """).df()
                
                if fallback_res.empty:
                    logger.warning(f"No market data (signals or price_history) for {ticker} on {today_str}. Skipping exit evaluation.")
                    continue
                mdata = fallback_res.iloc[0].to_dict()
                mdata['ensemble_score'] = None # No score available if not in signals table
                
            mdata_dict = {
                'current_score': mdata.get('ensemble_score'),
                'current_price': mdata.get('close_price', 0.0),
                'high_price': mdata.get('high_price', mdata.get('close_price', 0.0)),
                'highest_close': mdata.get('close_price', 0.0),  # Will use DB highest_close_since_t1 if available
                'atr_14': mdata.get('atr_14'),
                'rsi_14': mdata.get('rsi_14'),
                'pnl_pct': (mdata.get('close_price', 0.0) - pos['entry_price']) / pos['entry_price'] if pos['entry_price'] > 0 else 0
            }
            # Use DB-stored highest_close_since_t1 if available (for trailing stop)
            db_highest = pos.get('highest_close_since_t1')
            if db_highest and float(db_highest) > mdata_dict['highest_close']:
                mdata_dict['highest_close'] = float(db_highest)
                
            # --- V1.3 FIX: Fetch Almanac Data (Earnings) for Exit Veto ---
            earn_res = conn.execute(f"""
                SELECT next_earnings_date FROM yahoo.earnings_calendar 
                WHERE ticker = '{ticker}' AND fetched_at <= '{today_str}'
                ORDER BY fetched_at DESC LIMIT 1
            """).fetchone()
            if earn_res and earn_res[0]:
                next_earn = pd.to_datetime(earn_res[0]).date()
                pos['days_to_earnings'] = (next_earn - effective_date).days
            else:
                pos['days_to_earnings'] = None
            
            # --- V1.3 FIX: Fetch Live Quote for Precise Exit/Heal Evaluation ---
            current_price = mdata_dict['current_price']
            try:
                # In production, use the Alpaca Data Client to get the true current bid/ask/mid
                # For this context, we will attempt to fetch and use the latest quote
                quote_resp = data_client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=ticker))
                if ticker in quote_resp:
                    current_price = float(quote_resp[ticker].ask_price) # Conservative: use ask for exit calcs
                    if current_price <= 0:
                        logger.warning(f"  [WARN] {ticker}: Live quote is $0.00. Skipping lifecycle to avoid ghost liquidation.")
                        continue
                    mdata_dict['current_price'] = current_price
                    if not hasattr(client, '_sim_date'):
                        logger.info(f"  [LIVE QUOTE] {ticker}: Using Ask ${current_price:.2f} (Alpaca) vs ${mdata.get('close_price'):.2f} (DB)")
            except Exception as e:
                logger.warning(f"  [WARN] Failed to fetch live quote for {ticker}: {e}. Falling back to DB close.")

            # --- SUB-STEP 7.0: PROTECTION RECONCILIATION ---
            # Ensure every OPEN position has a Stop-Loss and Target in Alpaca
            heal_status = _heal_protection(pos, client, all_open_orders, market_data_lookup=market_data_lookup, simulate=simulate)
            
            # Consistent ID casting for all DB updates
            p_id = int(pos['id']) if pos.get('id') is not None and not pd.isna(pos.get('id')) else None

            if heal_status == "EMERGENCY_EXIT":
                # Logic: If healing triggered a market sell because the stop was violated, 
                # we must accurately log this exit and skip the rest of the evaluation loop for this ticker.
                exit_price = mdata_dict['current_price']
                p_nl_pct = (exit_price - pos['entry_price']) / pos['entry_price'] if pos['entry_price'] > 0 else 0
                p_nl_dollars = (exit_price - pos['entry_price']) * pos['shares']
                days = (effective_date - pd.to_datetime(pos['entry_date']).date()).days
                
                audit_results.append({
                    'ticker': ticker,
                    'action': 'SELL',
                    'reason': 'EMERGENCY_EXIT: Stop Violation',
                    'pnl_pct': p_nl_pct
                })
                
                if not simulate and p_id is not None:
                    conn.execute(f"""
                        UPDATE {config.E1_POSITIONS_TABLE} 
                        SET status = 'CLOSED', exit_date = ?, exit_price = ?,
                            exit_trigger = 'STOP_VIOLATION', exit_regime = ?,
                            pnl_pct = ?, pnl_dollars = ?, days_held = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, [today_str, exit_price, current_regime, p_nl_pct, p_nl_dollars, days, p_id])
                    
                    conn.execute(f"""
                        INSERT INTO {config.E1_TRADE_LOG_TABLE} (
                            position_id, ticker, action, trade_date, price, shares, dollar_value, 
                            trigger, reason, regime, pnl_pct, pnl_dollars, days_held
                        ) VALUES (
                            ?, ?, 'EXIT', ?, ?, ?, ?, 'STOP_VIOLATION', 'Gap down below stop-loss detected during reconciliation.', ?, ?, ?, ?
                        )
                    """, [p_id, ticker, today_str, exit_price, pos['shares'], exit_price * pos['shares'], current_regime, p_nl_pct, p_nl_dollars, days])
                
                closed_count += 1
                continue # Already handled this position
                
            elif heal_status == "ABORTED":
                logger.warning(f"  [SKIP] {ticker}: Heal aborted (no Alpaca position). Syncing DB to CLOSED.")
                if not simulate and p_id is not None:
                    conn.execute(f"""
                        UPDATE {config.E1_POSITIONS_TABLE} 
                        SET status = 'CLOSED', exit_date = '{today_str}',
                            exit_trigger = 'HEAL_ABORT_NO_POSITION',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = {p_id}
                    """)
                elif not simulate:
                    logger.warning(f"  [WARN] {ticker} skipped DB update for HEAL_ABORT_NO_POSITION due to NULL id")
                closed_count += 1
                continue
                
            elif heal_status == "FAILED":
                logger.warning(f"  [HEAL FAILED] {ticker}: Attempting fallback stop-only order.")
                stop_price = pos.get('stop_loss')
                
                if stop_price is None or math.isnan(stop_price):
                    logger.critical(f"  [EMERGENCY] {ticker}: Heal failed and no stop price available. Liquidating.")
                    if not simulate:
                        try:
                            client.submit_order(MarketOrderRequest(symbol=ticker, qty=int(pos['shares']), side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                            if not simulate:
                                notifier.send_telegram_alert(f"🚨 EMERGENCY EXIT {ticker}: heal failed and no stop price available. Position liquidated.")
                            if p_id is not None:
                                conn.execute(f"UPDATE {config.E1_POSITIONS_TABLE} SET status='CLOSED', exit_trigger='HEAL_FAILED_EMERGENCY_EXIT', updated_at=CURRENT_TIMESTAMP WHERE id={p_id}")
                        except Exception as e:
                            logger.critical(f"  [EMERGENCY FAIL] {ticker} market sell failed: {e}")
                    else:
                        logger.info(f"  [SIMULATE] Would liquidate {ticker} and send EMERGENCY EXIT alert due to missing stop_loss")
                else:
                    if not simulate:
                        try:
                            client.submit_order(StopOrderRequest(
                                symbol=ticker,
                                qty=int(pos['shares']),
                                side=OrderSide.SELL,
                                stop_price=round(float(stop_price), 2),
                                time_in_force=TimeInForce.GTC
                            ))
                            logger.info(f"  [HEAL FALLBACK] {ticker}: Successfully submitted stop-only order.")
                        except Exception as e:
                            logger.critical(f"  [MANUAL REVIEW] {ticker} — heal failed AND fallback stop failed. Position unprotected overnight.")
                            if not simulate:
                                notifier.send_telegram_alert(f"🚨 MANUAL REVIEW REQUIRED: {ticker} — heal failed AND fallback stop failed. Position unprotected overnight. Error: {e}")
                    else:
                        logger.info(f"  [SIMULATE] Would submit fallback stop-only order for {ticker} at ${stop_price:.2f}")
                continue

            eval_result = exit_evaluator.evaluate(pos, mdata_dict, current_regime, yesterday_regime, today=effective_date, conn=conn)
            
            # --- V1.3: Handle Stop Promotions (no exit, just DB update) ---
            if eval_result['action'] == 'ADVANCE_TO_BREAKEVEN':
                logger.info(f"  [{ticker}] STOP PROMOTION: INITIAL -> BREAKEVEN. New stop: ${eval_result['new_stop']:.2f}")
                if not simulate and p_id is not None:
                    conn.execute(f"""
                        UPDATE {config.E1_POSITIONS_TABLE} 
                        SET stop_stage = 'BREAKEVEN', stop_loss = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, [eval_result['new_stop'], p_id])
                    
                    # V1.3 FIX: Synchronize Alpaca immediately on promotion
                    try:
                        # Re-fetch position from DB to get the full updated state for healer
                        pos_updated = conn.execute(f"SELECT * FROM {config.E1_POSITIONS_TABLE} WHERE id = ?", [p_id]).df().to_dict('records')[0]
                        _heal_protection(pos_updated, client, all_open_orders, market_data_lookup=market_data_lookup, simulate=simulate)
                    except Exception as e:
                        logger.error(f"  [SYNC ERROR] Failed to push breakeven stop to Alpaca for {ticker}: {e}")
                elif not simulate:
                    logger.warning(f"  [WARN] {ticker} skipped DB update for ADVANCE_TO_BREAKEVEN due to NULL id")
                audit_results.append({'ticker': ticker, 'action': 'ADVANCE_TO_BREAKEVEN', 'reason': eval_result['reason'], 'pnl_pct': mdata_dict['pnl_pct']})
                continue
            
            if eval_result['action'] == 'UPDATE_TRAILING_STOP':
                new_stop = eval_result.get('new_stop', 0)
                new_mult = eval_result.get('new_trail_mult')
                logger.info(f"  [{ticker}] TRAILING UPDATE: stop -> ${new_stop:.2f}")
                if not simulate and p_id is not None:
                    update_sql = f"""
                        UPDATE {config.E1_POSITIONS_TABLE} 
                        SET stop_stage = 'TRAILING', trailing_stop = ?, stop_loss = ?,
                            highest_close_since_t1 = ?,
                            updated_at = CURRENT_TIMESTAMP
                    """
                    params = [new_stop, new_stop, mdata_dict['highest_close']]
                    if new_mult:
                        update_sql += f", trailing_mult_override = ?"
                        params.append(new_mult)
                    update_sql += f" WHERE id = ?"
                    params.append(p_id)
                    conn.execute(update_sql, params)
                    
                    # V1.3 FIX: Synchronize Alpaca immediately on trailing update
                    try:
                        pos_updated = conn.execute(f"SELECT * FROM {config.E1_POSITIONS_TABLE} WHERE id = ?", [p_id]).df().to_dict('records')[0]
                        _heal_protection(pos_updated, client, all_open_orders, market_data_lookup=market_data_lookup, simulate=simulate)
                    except Exception as e:
                        logger.error(f"  [SYNC ERROR] Failed to push trailing stop to Alpaca for {ticker}: {e}")
                elif not simulate:
                    logger.warning(f"  [WARN] {ticker} skipped DB update for UPDATE_TRAILING_STOP due to NULL id")
                audit_results.append({'ticker': ticker, 'action': 'UPDATE_TRAILING_STOP', 'reason': eval_result.get('reason', ''), 'pnl_pct': mdata_dict['pnl_pct']})
                continue
            
            # --- FIX 1: Alpaca Position Guardrail before Sell ---
            if eval_result['action'] in ('SELL', 'SELL_HALF', 'EMERGENCY_MARKET_EXIT', 'SUBMIT_STOP_LIMIT', 'CLOSE_POSITION'):
                alpaca_qty = alpaca_positions.get(ticker, 0)
                if alpaca_qty == 0:
                    logger.warning(f"  [SKIP] {ticker}: DB says OPEN but Alpaca has 0 shares. Marking CLOSED without order.")
                    if not simulate and p_id is not None:
                        conn.execute(f"UPDATE {config.E1_POSITIONS_TABLE} SET status = 'CLOSED', exit_trigger = 'ALPACA_DESYNC', updated_at = CURRENT_TIMESTAMP WHERE id = ?", [p_id])
                    elif not simulate:
                        logger.warning(f"  [WARN] {ticker} skipped DB update for ALPACA_DESYNC due to NULL id")
                    audit_results.append({
                        'ticker': ticker,
                        'action': 'CLOSED',
                        'reason': 'ALPACA_DESYNC: Position already zero in brokerage',
                        'pnl_pct': mdata_dict['pnl_pct']
                    })
                    closed_count += 1
                    continue
                    
            # Add to audit results
            audit_results.append({
                'ticker': ticker,
                'action': eval_result['action'],
                'reason': eval_result.get('reason', 'Thesis Intact'),
                'pnl_pct': mdata_dict['pnl_pct']
            })
            
            if eval_result['action'] in ('SELL', 'CLOSE_POSITION'):
                logger.info(f"EXITING {ticker}: {eval_result['reason']}")
                # Place market sell order
                if not simulate:
                    try:
                        client.submit_order(
                            MarketOrderRequest(
                                symbol=ticker,
                                qty=pos['shares'],
                                side=OrderSide.SELL,
                                time_in_force=TimeInForce.GTC
                            )
                        )
                    except Exception as e:
                        logger.error(f"FAILED to submit EXIT for {ticker}: {e}")
                        continue
                else:
                    logger.info(f"[SIMULATE] Would exit {ticker} ({pos['shares']} shares)")
                
                exit_price = eval_result.get('exit_price') or mdata_dict['current_price']
                pnl_pct = (exit_price - pos['entry_price']) / pos['entry_price'] if pos['entry_price'] > 0 else 0
                pnl_dollars = (exit_price - pos['entry_price']) * pos['shares']
                # V1.4: Use trading days instead of calendar days (F-14 fix)
                entry_dt = pd.to_datetime(pos['entry_date']).date() if hasattr(pos['entry_date'], 'strftime') else pos['entry_date']
                days_held = exit_evaluator.get_trading_days_held(conn, ticker, entry_dt, effective_date)
                
                # Update position status and log exit only if NOT simulating
                if not simulate and p_id is not None:
                    conn.execute(f"""
                        UPDATE {config.E1_POSITIONS_TABLE} 
                        SET status = 'CLOSED', exit_date = ?, exit_price = ?,
                            exit_trigger = ?, exit_regime = ?,
                            pnl_pct = ?, pnl_dollars = ?, days_held = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, [today_str, exit_price, eval_result.get('reason', 'UNKNOWN'), current_regime, pnl_pct, pnl_dollars, days_held, p_id])
                    
                    # --- V1.4: SCORE DECAY POST-EXIT TRACKING (Step 2) ---
                    if 'SCORE_DECAY_VETO' in str(eval_result.get('reason', '')):
                        try:
                            sim_run_id = getattr(_client, 'sim_run_id', None)
                            conn.execute(f"""
                                INSERT INTO {config.E1_DECAY_TRACKING_TABLE} (
                                    id, position_id, ticker, exit_date, exit_price, exit_pnl_pct, exit_pnl_dollars,
                                    entry_score, score_at_exit, entry_regime, sim_run_id
                                ) VALUES (
                                    nextval('e1_decay_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                                )
                            """, [
                                p_id, ticker, today_str, exit_price, pnl_pct, pnl_dollars,
                                pos.get('ensemble_score', 0.0), mdata_dict.get('current_score', 0.0), pos.get('entry_regime', 'UNKNOWN'),
                                sim_run_id
                            ])
                            logger.info(f"  [TRACKING] Logged {ticker} score decay for post-exit audit.")
                        except Exception as e:
                            logger.error(f"Failed to log score decay tracking for {ticker}: {e}")

                    # Update trade log for exits (F-01 consolidation)
                    trigger_val = eval_result.get('exit_trigger', eval_result.get('action', 'UNKNOWN'))
                    reason_val = eval_result.get('reason', 'UNKNOWN')
                    
                    conn.execute(f"""
                        INSERT INTO {config.E1_TRADE_LOG_TABLE} (
                            position_id, ticker, action, trade_date, price, shares, dollar_value, 
                            trigger, reason, regime, pnl_pct, pnl_dollars, days_held, sim_run_id
                        ) VALUES (
                            ?, ?, 'EXIT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                    """, [p_id, ticker, today_str, exit_price, pos['shares'], exit_price * pos['shares'], trigger_val, reason_val, current_regime, pnl_pct, pnl_dollars, days_held, sim_run_id])
                elif not simulate:
                    logger.warning(f"  [WARN] {ticker} skipped DB update for SELL due to NULL id")
                else:
                    logger.info(f"[SIMULATE] Would record exit for {ticker} in database.")

                closed_count += 1
        except Exception as e:
            logger.error(f"CRITICAL ERROR evaluating {ticker}: {e}", exc_info=True)
            continue
            


    # 8. ENTRY LOOP (Skip if manage_only)
    candidates = pd.DataFrame() # Initialized here to avoid unbound errors
    if not manage_only:
        # EMERGENCY BYPASS: Use signals_df directly (already filtered/ordered by SQL)
        entry_threshold = config.ENTRY_SCORE_THRESHOLD.get(current_regime, 0.65)
        candidates = signals_df[
            (signals_df['ensemble_score'] >= entry_threshold)
        ].copy()
        print(f"DEBUG: entry_threshold = {entry_threshold}")
        print(f"DEBUG: FINAL candidates length = {len(candidates)}")
    
    # Reload open positions with sector info (some might have just closed)
    open_positions_df = conn.execute(f"""
        SELECT p.*, COALESCE(t.sector, 'Miscellaneous') as mapped_sector 
        FROM {config.E1_POSITIONS_TABLE} p 
        LEFT JOIN refined.tickers t ON p.ticker = t.ticker 
        WHERE p.status = 'OPEN'
    """).df()
    open_positions = [row.to_dict() for _, row in open_positions_df.iterrows()]
    
    opened_count = 0
    deployed_dollars = 0.0
    processed_entries = []
    
    # ── BUDGETING 2.0: Portfolio Snapshot ────────────────────────────────
    # Calculate current Market Value and Stock Count per Sector AFTER exits
    sector_mv = {}
    sector_counts = {}
    for p in open_positions:
        sec = p.get('mapped_sector', 'Miscellaneous')
        if p.get('ticker') in config.MANUAL_SECTOR_OVERRIDES:
            sec = config.MANUAL_SECTOR_OVERRIDES[p.get('ticker')]
        sector_mv[sec] = sector_mv.get(sec, 0.0) + p.get('dollar_value', 0.0)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1


    if not candidates.empty:
        # Sort desc by conviction score
        candidates = candidates.sort_values(by='ensemble_score', ascending=False)
        
        # Load weights for cluster calculation
        try:
            regime_weights_all = load_regime_weights(WEIGHTS_JSON_PATH)
            regime_weights = regime_weights_all.get(current_regime, regime_weights_all.get('SAFE_DEFAULT'))
            if isinstance(regime_weights, tuple):
                signal_weights = regime_weights[0]
            else:
                signal_weights = regime_weights
        except Exception as e:
            logger.error(f"FATAL: Cannot load signal weights from {WEIGHTS_JSON_PATH}: {e}")
            if _conn is None:
                conn.close()
            return  # Hard stop — refuse to scan entries with unknown weights

        # 8.1 Strategy context check for entries
        if _weights_version == "unknown":
            logger.warning("Proceeding with 'unknown' weights version (No metadata found in JSON)")

        min_cash_pct = config.MIN_CASH_PCT_BY_REGIME.get(current_regime, config.E1_CASH_FLOOR)
        min_cash_required = portfolio_value * min_cash_pct

        for _, row in candidates.iterrows():
            ticker = row['ticker']

            # Canonical sector: use manual override if configured, else provider mapping.
            # Do NOT re-derive current_mapped_sector below this point in the entry loop.
            # Standardize sector retrieval (handle mapped_sector vs sector)
            # ── SECONDARY LOOKUP ──────────────────────────────────────────
            passed_sector = row.get('mapped_sector', row.get('sector'))
            if passed_sector in [None, 'None', 'NULL', '']:
                # Fallback to Tickers table if the signal data is missing sector info
                res = conn.execute(f"SELECT sector FROM refined.tickers WHERE ticker = '{ticker}'").fetchone()
                if res and res[0] not in [None, 'None', '']:
                    current_mapped_sector = res[0]
                    # logger.info(f"[{ticker}] Secondary Lookup Success: Found sector '{current_mapped_sector}'")
                else:
                    current_mapped_sector = 'Miscellaneous'
            else:
                current_mapped_sector = passed_sector

            if ticker in config.MANUAL_SECTOR_OVERRIDES:
                current_mapped_sector = config.MANUAL_SECTOR_OVERRIDES[ticker]
            
            logger.debug(f"{ticker} sector: {current_mapped_sector} (override={ticker in config.MANUAL_SECTOR_OVERRIDES})")

            # Calculate remaining budget for this sector dynamically
            budget_pct = config.E1_SECTOR_BUDGETS.get(current_mapped_sector, config.E1_SECTOR_BUDGETS.get('Other', 0.21))
            sector_budget_usd = portfolio_value * budget_pct
            current_mv = sector_mv.get(current_mapped_sector, 0.0)
            remaining_budget = sector_budget_usd - current_mv
            
            # Check Max Stocks backstop (Action 12)
            current_count = sector_counts.get(current_mapped_sector, 0)
            if current_count >= config.MAX_STOCKS_PER_SECTOR:
                logger.info(f"[{ticker}] VETO: Max stocks limit ({config.MAX_STOCKS_PER_SECTOR}) reached for {current_mapped_sector}.")
                continue

            # Budget-Limit Loop Guard (Drift-Resistant)
            if remaining_budget <= 0:
                # logger.debug(f"[{ticker}] VETO: Sector {current_mapped_sector} is at or over budget (${current_mv:,.0f} / ${sector_budget_usd:,.0f})")
                continue
            
            if cash_available <= min_cash_required:
                logger.info(f"Cash reserve floor reached (${min_cash_required:,.0f}). Stopping scan.")
                break
            # --- V1.3 FIX: Live Quote & Gap-Up Veto ---
            prev_close = row.get('close_price')
            live_price = prev_close
            try:
                # Use Ask for entry sizing (conservative)
                q_resp = data_client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=ticker))
                if ticker in q_resp and q_resp[ticker].ask_price > 0:
                    live_price = float(q_resp[ticker].ask_price)
                    if not hasattr(client, '_sim_date'):
                        logger.info(f"  [LIVE ENTRY QUOTE] {ticker}: ${live_price:.2f} (Ask) | PrevClose: ${prev_close:.2f}")
                else:
                    live_price = prev_close
                    logger.warning(f"  [LIVE ENTRY QUOTE FALLBACK] {ticker}: Using PrevClose ${live_price:.2f} (Alpaca Ask was 0 or missing)")
            except Exception as e:
                logger.warning(f"  [WARN] Failed live quote for {ticker}: {e}. Falling back to DB price.")

            # Gap-Up Staleness Veto
            if gap_up_veto(prev_close=prev_close, live_quote=live_price):
                logger.warning(f"  [GAP VETO] {ticker}: Gapped up significantly. Entry aborted to avoid staleness.")
                continue

            # Application of universal vetoes
            if not live_price or live_price < 1.00:
                logger.info(f"VETO: Price ({live_price}) < 1.00 for {ticker}")
                continue

            short_float = row.get('short_percent_of_float')
            if pd.notna(short_float) and short_float > 0.15:
                # logger.info(f"VETO: Short Float ({short_float:.2f}) for {ticker}")
                continue
                
            # Live Piotroski F-Score (hybrid multi-source)
            pio_result = piotroski.compute_piotroski_live(ticker, conn, sim_date=effective_date)
            pio_live = pio_result.get('f_score')
            if pio_result.get('warnings') and not hasattr(client, '_sim_date'):
                for w in pio_result['warnings']:
                    logger.info(f"{ticker}: Piotroski warning: {w}")
            # Write live score back to DB for downstream consumers
            piotroski.write_live_score(conn, ticker, pio_result)
            
            if pio_live is not None and pio_live <= 3:
                logger.info(f"VETO: Piotroski ({pio_live}) for {ticker}")
                continue
                
            rsi_14 = row.get('rsi_14')
                
            # BEAR-specific vetoes
            if current_regime == 'BEAR':
                drawdown = row.get('drawdown_52w')
                if pd.notna(drawdown) and drawdown <= -0.65:
                    continue
            # --- ALMANAC OVERLAY: Earnings Veto ---
            earnings_res = conn.execute(f"""
                SELECT next_earnings_date 
                FROM yahoo.earnings_calendar 
                WHERE ticker = '{ticker}' AND fetched_at <= '{today_str}'
                ORDER BY fetched_at DESC LIMIT 1
            """).fetchone()
            if earnings_res and earnings_res[0]:
                next_earnings = pd.to_datetime(earnings_res[0]).date()
                days_to_earnings = (next_earnings - effective_date).days
                if 0 <= days_to_earnings <= 5:
                    logger.info(f"VETO: Earnings in {days_to_earnings} days ({next_earnings}) for {ticker}")
                    continue
                
            # Check if we already own it
            if any(p['ticker'] == ticker for p in open_positions):
                continue
                
            # Sector is already identified securely at the top of the loop

            # Task 1: Dynamic Sector Cap Logic (Using pre-computed lookup)
            sector_rs = sector_rs_lookup.get(current_mapped_sector, 1.0)
            base_cap = config.E1_SECTOR_BUDGETS.get(current_mapped_sector, config.E1_SECTOR_BUDGETS.get('Other', 0.20))
            
            effective_cap_pct = e1_sizer.compute_dynamic_sector_cap(
                sector=current_mapped_sector,
                base_cap=base_cap,
                sector_rs=sector_rs,
                regime=current_regime
            )
            
            # Recalculate remaining budget with dynamic cap
            sector_budget_usd = portfolio_value * effective_cap_pct
            remaining_budget = sector_budget_usd - sector_mv.get(current_mapped_sector, 0.0)
            
            # Log adjustment if it occurred
            if effective_cap_pct != base_cap and not simulate:
                reason = "RS Leader Boost" if effective_cap_pct > base_cap else "RS Laggard Throttle"
                conn.execute(f"""
                    INSERT INTO sandbox.e1_sector_caps_history (date, regime, sector, base_cap, sector_rs, effective_cap, adjustment_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [today_str, current_regime, current_mapped_sector, base_cap, sector_rs, effective_cap_pct, reason])

            # Call sizer
            size_res = e1_sizer.compute_position_size(
                ticker=ticker,
                ensemble_score=row['ensemble_score'],
                close_price=live_price,
                atr_14=row.get('atr_14'),
                regime=current_regime,
                portfolio_value=portfolio_value,
                cash_available=cash_available,
                open_positions=open_positions,
                sector=current_mapped_sector,
                remaining_sector_budget=remaining_budget,
                vix_close=vix_close,
                hy_spread=hy_spread,
                skew_compression=skew_compression,
                sector_cap_pct=effective_cap_pct
            )
            
            if size_res['skipped']:
                logger.debug(f"Skipped {ticker}: {size_res['skip_reason']}")
                continue
                
            # --- MANDATORY INTERMEDIATE VALUE DUMP (§9) ---
            if not manage_only and len(processed_entries) < 5:
                logger.info(f"""
[AUDIT DUMP: {ticker}]
- raw_score:          {row['ensemble_score']:.4f}
- conviction_scalar:  {size_res.get('score_scalar', 0):.4f}
- risk_dollars:       {size_res.get('dollar_value', 0) / (size_res.get('shares', 1) * live_price) * 100 if live_price else 0:.2f}% (effective)
- atr_dollars:        {row.get('atr_14', 0):.4f}
- stop_distance:      {size_res.get('stop_loss', 0) - live_price:.4f}
- raw_shares:         {size_res.get('shares', 0)}
- floored_shares:     {size_res.get('shares', 0)}
- final_position_size: ${size_res.get('dollar_value', 0):,.2f}
-----------------------------------------------------------""")
                processed_entries.append(ticker)
                
            # Passed all gates and sizing
            shares = size_res['shares']
            dollar_val = size_res['dollar_value']
            
            # Use pre-scored signal columns from ensemble_daily_scores (Action 2)
            votes_computed = {
                'sig_ma_crossover':      row.get('sig_ma_crossover', 0),
                'sig_rs_3month':         row.get('sig_rs_3month', 0),
                'sig_sector_momentum':   row.get('sig_sector_momentum', 0),
                'sig_ma_slope':          row.get('sig_ma_slope', 0),
                'sig_rsi_oversold':      row.get('sig_rsi_oversold', 0),
                'sig_drawdown_recovery': row.get('sig_drawdown_recovery', 0),
                'sig_fundamental':       row.get('sig_fundamental', 0),
            }
            # V1.3 Dominant Cluster & Lifecycle Initialization
            dominant_cluster, cluster_dominance_pct = signal_votes.compute_dominant_cluster(votes_computed, signal_weights)
            
            # V1.3 Entry Levels: cluster-specific stops/targets with frozen ATR
            atr_14_val = row.get('atr_14', live_price * 0.03)
            entry_levels = e1_sizer.compute_entry_levels(live_price, atr_14_val, dominant_cluster)
            
            initial_stop = entry_levels['initial_stop']
            breakeven_trigger = entry_levels['breakeven_trigger']
            t1_target = entry_levels['t1_target']
            t2_target = entry_levels['t2_target']
            atr_at_entry = entry_levels['atr_at_entry']
            stop_loss = initial_stop  # Initial stop IS the stop loss
            
            # Use safety checks for targets to avoid NaN issues
            if t1_target is None or math.isnan(t1_target): 
                t1_target = live_price * 1.10
            if t2_target is None or math.isnan(t2_target):
                t2_target = live_price * 1.20
            
            target_1 = t1_target
            target_2 = t2_target
            
            # Flat 20-day horizon (Config D)
            max_hold_days = 20
            
            logger.info(f"ENTERING {ticker} — Total {shares} shares @ ${live_price:.2f} (Score: {row['ensemble_score']:.2f})")
            logger.info(f"  -> Consolidated Order: {shares} shares | Target (T2): ${target_2:.2f} | Stop (Circuit Breaker): ${stop_loss:.2f}")

            # ── Last-Mile Execution Guard (Action 12) ───────────────────────────
            # Re-verify sector budget one last time (already handled by remaining_budget check, 
            # but this catches drift that might have happened during the scan loop itself)
            if sector_mv.get(current_mapped_sector, 0.0) + dollar_val > (portfolio_value * budget_pct) + 1.0: # $1.0 buffer for float math
                logger.warning(f"LAST-MILE VETO: {ticker} bypassed. Sector '{current_mapped_sector}' budget was hit by a previous entry in this same loop.")
                continue

            if simulate:
                logger.info(f"[SIMULATE] Would insert DB record and submit Consolidated Bracket Order for {ticker}")
                logger.info(f"  -> V1.3 Would store: cluster={dominant_cluster}, stop={initial_stop:.2f}, BE={breakeven_trigger:.2f}, T1={t1_target:.2f}, T2={t2_target:.2f}")
                cash_available -= dollar_val
                sector_mv[current_mapped_sector] = sector_mv.get(current_mapped_sector, 0.0) + dollar_val
                sector_counts[current_mapped_sector] = sector_counts.get(current_mapped_sector, 0) + 1
                pos_dict = {'ticker': ticker, 'sector': current_mapped_sector, 'mapped_sector': current_mapped_sector, 'dollar_value': dollar_val}
                open_positions.append(pos_dict)
                opened_count += 1
                deployed_dollars += dollar_val
                continue

            # --- TRANSACTIONAL ENTRY (F-07) ---
            # Step 1: Database Insert First
            try:
                res = conn.execute(f"""
                INSERT INTO {config.E1_POSITIONS_TABLE} (
                    ticker, status, entry_date, entry_price, shares, dollar_value, 
                    ensemble_score, entry_score, entry_regime, dominant_cluster, cluster_dominance_pct, max_hold_days,
                    stop_loss, target_1, target_2, target_1_hit, score_scalar,
                    initial_stop, breakeven_trigger, stop_stage, t1_price, t2_price,
                    atr_at_entry, shares_total, shares_remaining,
                    vote_signal_1, vote_signal_2, vote_signal_3, vote_signal_4, vote_signal_5, vote_signal_6, vote_signal_7,
                    weights_version, sim_run_id, sim_date
                ) VALUES (
                    ?, 'OPEN', ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, FALSE, ?,
                    ?, ?, 'INITIAL', ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                ) RETURNING id
                """, [
                    ticker, today_str, live_price, shares, dollar_val,
                    row['ensemble_score'], row['ensemble_score'], current_regime, dominant_cluster, cluster_dominance_pct, max_hold_days,
                    stop_loss, target_1, target_2, size_res['score_scalar'],
                    initial_stop, breakeven_trigger, t1_target, t2_target,
                    atr_at_entry, shares, shares,
                    votes_computed.get('sig_ma_crossover', 0), votes_computed.get('sig_rs_3month', 0), votes_computed.get('sig_sector_momentum', 0),
                    votes_computed.get('sig_ma_slope', 0), votes_computed.get('sig_rsi_oversold', 0), votes_computed.get('sig_drawdown_recovery', 0),
                    votes_computed.get('sig_fundamental', 0), _weights_version,
                    sim_run_id, today_str if _sim_date else None
                ]).fetchone()
                pos_id = res[0]
            except Exception as e:
                logger.error(f"DB INSERT FAILED for {ticker}: {e}. Skipping order submission.")
                continue

            # Step 2: Order Submission
            try:
                client.submit_order(
                    MarketOrderRequest(
                        symbol=ticker,
                        qty=int(shares),
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.GTC,
                        order_class=OrderClass.BRACKET,
                        take_profit=TakeProfitRequest(limit_price=round(target_2, 2)),
                        stop_loss=StopLossRequest(stop_price=round(stop_loss, 2))
                    )
                )
            except Exception as e:
                logger.critical(f"ORDER SUBMISSION FAILED for {ticker} after DB insert. Rolling back DB record. Error: {e}")
                conn.execute(f"DELETE FROM {config.E1_POSITIONS_TABLE} WHERE id = ?", [pos_id])
                try:
                    if not simulate:
                        notifier.send_telegram_alert(f"⚠ Entry ORDER FAILED for {ticker}: {e}. DB record rolled back. No position opened.")
                except Exception:
                    pass
                continue
            
            # Step 3: Update loop state and dependent DB records
            cash_available -= dollar_val
            sector_mv[current_mapped_sector] = sector_mv.get(current_mapped_sector, 0.0) + dollar_val
            sector_counts[current_mapped_sector] = sector_counts.get(current_mapped_sector, 0) + 1
            pos_dict = {
                'ticker': ticker,
                'sector': current_mapped_sector,
                'mapped_sector': current_mapped_sector,
                'dollar_value': dollar_val
            }
            open_positions.append(pos_dict)
            
            # V1.3: Trade Log entry
            conn.execute(f"""
                INSERT INTO {config.E1_TRADE_LOG_TABLE} (
                    position_id, ticker, action, trade_date, price, shares, dollar_value, regime,
                    ensemble_score, dominant_cluster, stop_loss, target_1, target_2, score_scalar,
                    vote_signal_1, vote_signal_2, vote_signal_3, vote_signal_4, vote_signal_5, vote_signal_6, vote_signal_7,
                    weights_version, sim_run_id
                ) VALUES (
                    ?, ?, 'ENTRY', ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?
                )
            """, [
                pos_id, ticker, today_str, live_price, shares, dollar_val, current_regime,
                row['ensemble_score'], dominant_cluster, stop_loss, target_1, target_2, size_res['score_scalar'],
                votes_computed.get('sig_ma_crossover', 0), votes_computed.get('sig_rs_3month', 0), votes_computed.get('sig_sector_momentum', 0),
                votes_computed.get('sig_ma_slope', 0), votes_computed.get('sig_rsi_oversold', 0), votes_computed.get('sig_drawdown_recovery', 0),
                votes_computed.get('sig_fundamental', 0), _weights_version,
                sim_run_id
            ])
            
            # V1.3: Position Fills entry (ENTRY event)
            fill_id = str(uuid.uuid4())[:8]
            try:
                spy_price = conn.execute("SELECT close FROM refined.price_history WHERE ticker = 'SPY' AND date = (SELECT MAX(date) FROM refined.price_history WHERE ticker = 'SPY')").fetchone()
                spy_at_fill = spy_price[0] if spy_price else 0.0
            except Exception:
                spy_at_fill = 0.0
                
            conn.execute(f"""
                INSERT INTO {config.E1_FILLS_TABLE} (
                    fill_id, position_id, ticker, fill_date, fill_type, shares,
                    fill_price, dollar_value, stop_stage_at_fill, spy_price_at_fill, notes
                ) VALUES (
                    ?, ?, ?, ?, 'ENTRY', ?,
                    ?, ?, 'INITIAL', ?, ?
                )
            """, [
                fill_id, pos_id, ticker, today_str, shares,
                live_price, dollar_val, spy_at_fill,
                f'V1.3 entry: cluster={dominant_cluster}, ATR={atr_at_entry:.4f}, stop_mult={entry_levels["stop_mult_used"]}x'
            ])
            
            logger.info(f"  -> V1.3: Stored entry_levels (stop={initial_stop:.2f}, BE={breakeven_trigger:.2f}, T1={t1_target:.2f}, T2={t2_target:.2f}, ATR_frozen={atr_at_entry:.4f})")
            
            opened_count += 1
            deployed_dollars += dollar_val
    else:
        logger.info("Morning Audit Mode: Skipping new candidate entries.")
        opened_count = 0
        deployed_dollars = 0

    # 9. FINAL STEP: Beta Sweeper Orchestration (Task 2)
    # The Beta Sweeper checks for idle cash and fills exposure to 95% in Healthy regimes.
    execute_beta_sweeper(client, conn, portfolio_value, cash_available, current_regime, simulate=simulate, as_of_date=_sim_date)

    # 10. POST-EXECUTION RECONCILIATION ("The Double-Sync Sandwich")
    # Captures fills from this scan, syncs history, and sends the final verified summary.
    # Close conn only if we opened it ourselves (not injected by shadow runner)
    if _conn is None:
        conn.close()
    run_e1_reconciler(simulate=simulate, _client=_client, _conn=_conn, _sim_date=_sim_date)

    logger.info(f"E1 Trader complete. Positions closed: {closed_count}, Positions opened: {opened_count}")
    logger.info(f"Capital deployed today: ${deployed_dollars:,.2f}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Strategy E1 Paper Trader")
    parser.add_argument('--simulate', action='store_true', help="Run without placing orders or updating DB")
    parser.add_argument('--manage-only', action='store_true', help="Only process exits for open positions; skip scanning for new entries")
    args = parser.parse_args()
    
    run_e1_trader(simulate=args.simulate, manage_only=args.manage_only)
