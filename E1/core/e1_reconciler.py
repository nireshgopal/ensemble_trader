import os
import sys
import logging
from datetime import datetime, date
import duckdb
import pandas as pd
import dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest, StopOrderRequest, LimitOrderRequest, 
    TakeProfitRequest, StopLossRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus, OrderClass
import argparse

# Load environment variables (API Keys)
dotenv.load_dotenv()

# Ensure the root of the project is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# We use absolute imports for the Sandbox build (E1 package)
from E1.core import notifier, config, e1_monitor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# DB_PATH removed, using config.DB_PATH

def run_e1_reconciler(simulate=False, _client=None, _conn=None, _sim_date=None):
    logger.info("=" * 60)
    logger.info(f"  STRATEGY E1: EOD RECONCILIATION {'(SHADOW MODE)' if _client else '(SIMULATED)' if simulate else ''}")
    logger.info("=" * 60)

    # 1. Initialize Alpaca Client for E1
    # Shadow Mode: accept injected mock client; fall back to real Alpaca in production
    if _client is not None:
        client = _client
        logger.info("[SHADOW MODE] Reconciler using injected MockAlpacaClient — no real Alpaca calls")
        account = client.get_account()
    else:
        api_key = os.getenv('E1_ALPACA_KEY')
        api_secret = os.getenv('E1_ALPACA_SECRET')
        if not api_key or not api_secret:
            logger.error("E1_ALPACA_KEY or E1_ALPACA_SECRET not set. Reconciliation aborted.")
            return
        client = TradingClient(api_key, api_secret, paper=True)
        account = client.get_account()

    logger.info(f"Reconciling for Alpaca Account: {account.account_number} (Status: {account.status})")
    
    try:
        positions = client.get_all_positions()
        logger.info(f"Fetched {len(positions)} open positions from Strategy E1 account.")
    except Exception as e:
        logger.error(f"Could not fetch positions: {e}")
        return

    # Shadow Mode: accept injected connection; fall back to new real connection in production
    conn = _conn if _conn is not None else duckdb.connect(config.DB_PATH)

    # Ensure Decay Exit Tracking Table exists
    conn.execute(f"CREATE SEQUENCE IF NOT EXISTS e1_decay_seq")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {config.E1_DECAY_TRACKING_TABLE} (
            id INTEGER PRIMARY KEY DEFAULT nextval('e1_decay_seq'),
            position_id INTEGER,
            ticker VARCHAR,
            exit_date DATE,
            exit_price FLOAT,
            exit_pnl_pct FLOAT,
            exit_pnl_dollars FLOAT,
            entry_score FLOAT,
            score_at_exit FLOAT,
            entry_regime VARCHAR,
            price_5d_post FLOAT,
            price_10d_post FLOAT,
            price_20d_post FLOAT,
            pnl_5d_counterfactual FLOAT,
            pnl_10d_counterfactual FLOAT,
            pnl_20d_counterfactual FLOAT,
            verdict VARCHAR,
            verdict_date DATE,
            sim_run_id VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Schema Migration: Add sim_run_id if it doesn't exist (F-04 recovery)
    try:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{config.E1_DECAY_TRACKING_TABLE}')").fetchall()]
        if 'sim_run_id' not in cols:
            conn.execute(f"ALTER TABLE {config.E1_DECAY_TRACKING_TABLE} ADD COLUMN sim_run_id VARCHAR")
            logger.info(f"Schema Migration: Added sim_run_id to {config.E1_DECAY_TRACKING_TABLE}")
    except Exception as e:
        logger.warning(f"Decay tracking schema check skipped: {e}")


    today_dt = _sim_date if _sim_date else date.today()
    today_str = today_dt.isoformat()

    # Reconciliation Staleness Guard
    # Shadow Mode: use sim_date as reference; Production: use today/yesterday
    try:
        from datetime import timedelta
        if _sim_date:
            # In shadow mode: confirm price data exists on or before sim_date
            sim_price_date = conn.execute(
                "SELECT MAX(date) FROM refined.price_history WHERE date <= ?", [_sim_date]
            ).fetchone()[0]
            if sim_price_date is None:
                logger.warning(f"[SHADOW] No price_history at or before {today_str}. Skipping reconciler.")
                return
            logger.info(f"[SHADOW] Price history freshness OK for sim_date {today_str}: closest={sim_price_date}")
        else:
            max_price_date = conn.execute(
                "SELECT MAX(date) FROM refined.price_history"
            ).fetchone()[0]
            max_price_str = str(max_price_date)
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            if max_price_str not in (today_str, yesterday):
                logger.error(
                    f"RECONCILIATION STALENESS GUARD: price_history max date is "
                    f"{max_price_str}. Expected {yesterday} or {today_str}. "
                    f"Aborting reconciliation to prevent ghost stop closures."
                )
                if _conn is None:
                    conn.close()
                return
            logger.info(f"Price history freshness OK: {max_price_str}")
    except Exception as e:
        logger.error(f"Could not verify price_history freshness: {e}. Aborting.")
        if _conn is None:
            conn.close()
        return

    # 2. Sync Order History
    try:
        # Fetch recent orders (all statuses)
        order_request = GetOrdersRequest(status=QueryOrderStatus.ALL, nested=True)
        all_orders = client.get_orders(filter=order_request)
        logger.info(f"Fetched {len(all_orders)} recent orders for history sync.")
        
        if all_orders and not simulate:
            # Prepare data for bulk insert
            order_data = []
            for o in all_orders:
                order_data.append((
                    str(o.id),
                    o.client_order_id,
                    o.symbol,
                    str(o.side.value) if hasattr(o.side, 'value') else str(o.side),
                    int(float(o.qty)) if o.qty else 0,
                    str(o.status.value) if hasattr(o.status, 'value') else str(o.status),
                    int(float(o.filled_qty)) if o.filled_qty else 0,
                    float(o.filled_avg_price) if o.filled_avg_price else 0.0,
                    o.created_at,
                    datetime.now()
                ))
            
            # Use temporary table for bulk ON CONFLICT update
            conn.execute("CREATE TEMPORARY TABLE temp_order_sync AS SELECT * FROM " + config.E1_ORDER_HISTORY_TABLE + " WHERE 1=0")
            
            # Standard insert into temp
            conn.executemany(f"""
                INSERT INTO temp_order_sync
                (order_id, client_order_id, ticker, side, qty, status, filled_qty, filled_avg_price, submitted_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, order_data)
            
            # Bulk upsert from temp
            conn.execute(f"""
                INSERT INTO {config.E1_ORDER_HISTORY_TABLE}
                SELECT * FROM temp_order_sync
                ON CONFLICT (order_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    filled_qty = EXCLUDED.filled_qty,
                    filled_avg_price = EXCLUDED.filled_avg_price,
                    updated_at = EXCLUDED.updated_at
            """)
            conn.execute("DROP TABLE temp_order_sync")
        elif simulate:
            for o in all_orders[:5]: # Limit logs
                logger.info(f"      [SIMULATE] Would sync order history for {o.symbol} ({o.status})")
            if len(all_orders) > 5:
                logger.info(f"      [SIMULATE] ... and {len(all_orders)-5} more.")
                
        logger.info(f"Successfully synced E1 physical order receipts into '{config.E1_ORDER_HISTORY_TABLE}'.")
    except Exception as e:
        logger.error(f"Error syncing E1 order history: {e}")

    # 3. Sync e1_positions Snapshot
    logger.info(f"Reconciling '{config.E1_POSITIONS_TABLE}' against Alpaca...")
    
    # Get set of tickers currently in Alpaca
    alpaca_tickers = {p.symbol for p in positions}
    
    # Mark positions in DB that are NO LONGER in Alpaca as CLOSED
    if not simulate:
        conn.execute(f"""
            UPDATE {config.E1_POSITIONS_TABLE} 
            SET status = 'CLOSED', exit_date = '{today_str}', exit_trigger = 'RECONCILIATION_SYNC'
            WHERE status = 'OPEN' AND ticker NOT IN ({', '.join([f"'{t}'" for t in alpaca_tickers]) if alpaca_tickers else "''"})
        """)
    else:
        logger.info(f"  [SIMULATE] Would mark tickers not in Alpaca as CLOSED.")
    
    for p in positions:
        ticker = p.symbol
        qty_raw = float(p.qty)
        qty = int(qty_raw)
        
        if qty_raw < 0:
            logger.error(f"  [CRITICAL] {ticker}: Detect SHORT position ({qty_raw}) in E1 account! Strategy violation.")
            # We don't use abs() here; we want the DB to reflect the actual negative shares
            # but we'll flag it for emergency attention.
        
        entry_price = float(p.avg_entry_price)
        market_val = float(p.market_value)
        
        # Check if we have this open in DB
        db_pos = conn.execute(f"""
            SELECT p.id, p.stop_loss, COALESCE(t.sector, 'Miscellaneous') as mapped_sector
            FROM {config.E1_POSITIONS_TABLE} p
            LEFT JOIN refined.tickers t ON p.ticker = t.ticker
            WHERE p.ticker = ? AND p.status = 'OPEN'
        """, [ticker]).fetchone()
        
        if db_pos:
            # Check for unmapped sectors to build the manual override list
            mapped_sector = db_pos[2]
            if ticker in config.MANUAL_SECTOR_OVERRIDES:
                mapped_sector = config.MANUAL_SECTOR_OVERRIDES[ticker]
            if mapped_sector in ['Other', 'Miscellaneous', 'None', None]:
                logger.warning(f"  [SECTOR GOVERNANCE] {ticker} mapped to '{mapped_sector}'. Consider adding to config.MANUAL_SECTOR_OVERRIDES.")
            # Update existing shares (but DO NOT overwrite cost basis/dollar_value)
            if not simulate:
                conn.execute(f"UPDATE {config.E1_POSITIONS_TABLE} SET shares = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", [qty, db_pos[0]])
            else:
                logger.info(f"      [SIMULATE] Would update {ticker} shares to {qty}")
        else:
            closed_today = conn.execute(
                f"SELECT id FROM {config.E1_POSITIONS_TABLE} WHERE ticker = ? AND status = 'CLOSED' AND exit_date = ?",
                [ticker, today_str]
            ).fetchone()
            
            if closed_today:
                logger.warning(f"  [SKIP] {ticker}: Found in Alpaca but closed today in DB. Likely settlement lag.")
                continue
                
            # Missing in DB? Handle as sync
            logger.warning(f"Found {ticker} in Alpaca but NOT in '{config.E1_POSITIONS_TABLE}'. Creating placeholder...")
            if not simulate:
                # Use current price as entry price and 5% fallback stop for synchronized rows
                entry_price = float(p.avg_entry_price)
                market_val = float(p.market_value)
                stop_price = entry_price * 0.95
                conn.execute(f"""
                    INSERT INTO {config.E1_POSITIONS_TABLE} (ticker, status, entry_date, entry_price, shares, dollar_value, ensemble_score, entry_regime, dominant_cluster, stop_loss, score_scalar)
                    VALUES (?, 'OPEN', ?, ?, ?, ?, 0.0, 'UNKNOWN', 'SYNCHRONIZED', ?, 1.0)
                """, [ticker, today_str, entry_price, qty, market_val, stop_price])
            else:
                logger.info(f"      [SIMULATE] Would create missing DB row for {ticker}")

    # 4. Two-Way Healing Pass (Re-submit missing Alpaca stops)
    logger.info("HEALING PASS: Verifying protective stops for E1...")
    
    # Get all open sell orders for E1
    open_order_request = GetOrdersRequest(status=QueryOrderStatus.OPEN, side=OrderSide.SELL)
    open_sell_orders = client.get_orders(filter=open_order_request)
    open_sell_tickers = {o.symbol for o in open_sell_orders}
    
    for p in positions:
        ticker = p.symbol
        qty = int(float(p.qty))
        
        # Get actual open orders for this ticker
        ticker_orders = [o for o in open_sell_orders if o.symbol == ticker]
        has_stop = any(o.type == 'stop' for o in ticker_orders)
        has_limits = [round(float(o.limit_price), 2) for o in ticker_orders if o.type == 'limit' and o.limit_price]
        
        # Fetch the targets and stops from DB
        row = conn.execute(f"SELECT stop_loss, target_2, shares FROM {config.E1_POSITIONS_TABLE} WHERE ticker=? AND status='OPEN'", [ticker]).fetchone()
        
        if row:
            db_stop, db_t2, db_shares = row
            # Filter for missing safety legs
            needs_stop = not has_stop
            needs_target = db_t2 is not None and round(float(db_t2), 2) not in has_limits
            
            if needs_stop or needs_target:
                logger.warning(f"  [!] {ticker}: Protection Gap (Stop:{needs_stop}, Target:{needs_target}). HEALING...")
                
                try:
                    if not simulate:
                        # Cancel ALL sell orders for this ticker to unlock quantity completely
                        for o in ticker_orders:
                            try: client.cancel_order_by_id(o.id)
                            except: pass
                        
                        if not hasattr(client, '_sim_date'):
                            import time
                            time.sleep(1.5)

                        if db_stop is None or db_t2 is None:
                            logger.warning(f"  [SKIP] {ticker}: Missing stop/target in DB. Cannot heal.")
                            continue

                        # Submit Consolidated OCO Order (Target 2 + Stop)
                        client.submit_order(LimitOrderRequest(
                            symbol=ticker, qty=qty, side=OrderSide.SELL,
                            limit_price=round(db_t2, 2), time_in_force=TimeInForce.GTC,
                            order_class=OrderClass.OCO,
                            take_profit=TakeProfitRequest(limit_price=round(db_t2, 2)),
                            stop_loss=StopLossRequest(stop_price=round(db_stop, 2))
                        ))
                        logger.info(f"      ✓ Restored Consolidated OCO (Target 2 + Stop) for {ticker}")
                    else:
                        logger.info(f"      [SIMULATE] Would HEAL {ticker} protection legs with a single bracket.")
                except Exception as e:
                    logger.error(f"      FAILED to heal E1 {ticker}: {e}")
        else:
            logger.debug(f"    [PASS] {ticker}: Protective orders are active.")


    # 5. EOD PORTFOLIO SUMMARY
    try:
        logger.info("Generating EOD Portfolio Summary...")
        account = client.get_account()
        # Fetch fresh positions for the summary
        summary_positions = client.get_all_positions()
        
        summary_msg = notifier.format_portfolio_summary(
            date_str=today_str,
            account=account,
            positions=summary_positions
        )
        
        # V1.4: Add Score Decay Audit Summary
        try:
            decay_stats_rows = conn.execute(f"""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN verdict = 'VETO_SAVED_CAPITAL' THEN 1 ELSE 0 END) as saved_count,
                    SUM(CASE WHEN verdict = 'VETO_COST_ALPHA' THEN 1 ELSE 0 END) as cost_count,
                    SUM(CASE WHEN verdict = 'VETO_COST_ALPHA_NOISE' THEN 1 ELSE 0 END) as noise_count,
                    AVG(CASE WHEN verdict = 'VETO_COST_ALPHA' THEN (price_20d_post - exit_price) * (exit_pnl_dollars / NULLIF(exit_pnl_pct, 0)) ELSE 0 END) as avg_cost_dollars
                FROM {config.E1_DECAY_TRACKING_TABLE}
                WHERE verdict IS NOT NULL
            """).df()
            
            if not decay_stats_rows.empty and decay_stats_rows['total'].iloc[0] > 0:
                stats_row = decay_stats_rows.iloc[0]
                total = int(stats_row['total'])
                
                # Requires n>=45 to keep false positive rate below 5% (binomial, p=0.50)
                # Power analysis: P(X>=18|n=30,p=0.50) = 18%; P(X>=23|n=45,p=0.50) = 5%
                if total < 45:
                    logger.info(f"Score decay audit skipped (n={total} < 45 minimum sample size).")
                
                saved = int(stats_row['saved_count'])
                cost = int(stats_row['cost_count'])
                noise = int(stats_row.get('noise_count', 0))
                avg_cost = float(stats_row['avg_cost_dollars']) if not pd.isna(stats_row['avg_cost_dollars']) else 0.0
                
                stats_dict = {
                    'total': total,
                    'saved_count': saved,
                    'cost_count': cost,
                    'noise_count': noise,
                    'saved_pct': (saved / total) * 100,
                    'cost_pct': (cost / total) * 100,
                    'avg_cost_dollars': avg_cost
                }
                summary_msg += notifier.format_decay_audit_summary(stats_dict)
        except Exception as e:
            logger.warning(f"Could not include Score Decay audit in summary: {e}")

        if not simulate:
            notifier.send_telegram(summary_msg)
            logger.info("EOD Portfolio Summary sent to Telegram.")
        else:
            logger.info("🧪 [SIM] EOD Portfolio Summary generated (Telegram bypassed).")
    except Exception as e:
        logger.error(f"Failed to generate/send EOD Portfolio Summary: {e}")

    # 6. V1.3: STOP STATE CONSISTENCY CHECK
    verify_stop_state_consistency(conn, client, simulate)

    # 7. V1.4: ALPHA MONITORING (Rolling 30-Session Audit)
    try:
        logger.info("Triggering EOD Performance Monitor...")
        e1_monitor.run_performance_monitor(simulate=simulate, sim_date=today_dt, conn=conn)
    except Exception as e:
        logger.error(f"Alpha Monitor trigger failed: {e}")

    # 8. V1.4: SCORE DECAY POST-EXIT BACKFILL
    if not simulate:
        try:
            logger.info("Running Score Decay post-exit backfill...")
            backfill_decay_exit_verdicts(conn, today_dt)
        except Exception as e:
            logger.error(f"Score Decay backfill failed: {e}")

    if _conn is None:
        conn.close()
    logger.info("Strategy E1 Reconciliation complete.")

    logger.info("=" * 60)


def verify_stop_state_consistency(conn, client, simulate=False):
    """
    V1.3 Stop State Reconciler.
    For each open sandbox position, verify Alpaca has a matching stop order.
    Logs divergences to sandbox.e1_reconciler_flags.
    """
    import uuid
    
    logger.info("--- V1.3 Stop State Consistency Check ---")
    
    try:
        open_positions = conn.execute(
            f"SELECT id, ticker, stop_loss, stop_stage, initial_stop, trailing_stop "
            f"FROM {config.E1_POSITIONS_TABLE} WHERE status = 'OPEN'"
        ).fetchall()
    except Exception as e:
        logger.error(f"Could not fetch open positions for stop check: {e}")
        return
    
    if not open_positions:
        logger.info("  No open positions to check.")
        return
    
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        all_orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
    except Exception as e:
        logger.error(f"Could not fetch Alpaca orders for stop check: {e}")
        return
    
    # Use sim_date from client if available, otherwise date.today()
    _sim_date = getattr(client, '_sim_date', None)
    today_dt = _sim_date if _sim_date else date.today()
    today_str = today_dt.isoformat()
    divergences = 0
    
    for pos in open_positions:
        pos_id, ticker, db_stop, stage, initial_stop, trailing_stop = pos
        
        # Find stop orders for this ticker (checking both top-level and nested legs)
        ticker_stops = []
        for o in all_orders:
            if o.symbol == ticker:
                if 'stop' in str(o.type).lower():
                    ticker_stops.append(o)
                for leg in (o.legs or []):
                    if 'stop' in str(leg.type).lower():
                        ticker_stops.append(leg)
        
        if not ticker_stops:
            logger.warning(f"  [DIVERGENCE] {ticker}: No stop order found in Alpaca. DB stop_stage={stage}")
            if not simulate:
                flag_id = str(uuid.uuid4())[:8]
                conn.execute(f"""
                    INSERT INTO {config.E1_RECONCILER_FLAGS_TABLE} (
                        flag_id, position_id, flag_date, flag_type, db_value, alpaca_value, resolved, notes
                    ) VALUES (
                        '{flag_id}', '{pos_id}', '{today_str}', 'NO_STOP_ORDER',
                        'stop_stage={stage}, stop_loss={db_stop}', 'NO_STOP_FOUND', FALSE,
                        'V1.3 reconciler: No matching stop order in Alpaca for open position.'
                    )
                """)
            divergences += 1
            continue
        
        # Check stop price alignment (use the closest order)
        for stop_order in ticker_stops:
            alpaca_stop_price = float(stop_order.stop_price) if stop_order.stop_price else 0
            expected_stop = db_stop if db_stop else (trailing_stop or initial_stop or 0)
            
            if expected_stop and expected_stop > 0 and alpaca_stop_price > 0:
                price_diff_pct = abs(alpaca_stop_price - expected_stop) / expected_stop
                if price_diff_pct > 0.005:  # >0.5% divergence
                    logger.warning(f"  [DIVERGENCE] {ticker}: Stop price mismatch. DB={expected_stop:.2f} vs Alpaca={alpaca_stop_price:.2f} ({price_diff_pct:.2%})")
                    if not simulate:
                        flag_id = str(uuid.uuid4())[:8]
                        conn.execute(f"""
                            INSERT INTO {config.E1_RECONCILER_FLAGS_TABLE} (
                                flag_id, position_id, flag_date, flag_type, db_value, alpaca_value, resolved, notes
                            ) VALUES (
                                '{flag_id}', '{pos_id}', '{today_str}', 'STOP_PRICE_DIVERGENCE',
                                '{expected_stop:.4f}', '{alpaca_stop_price:.4f}', FALSE,
                                'V1.3 reconciler: Stop price mismatch > 0.5%. TRIGGERING HEAL.'
                            )
                        """)
                        
                        # --- V1.3 FIX: Active Healing of Price Mismatches ---
                        logger.warning(f"  [HEAL] {ticker}: Triggering protection refresh for price alignment.")
                        try:
                            # Re-submit consolidated OCO (Target 2 + Stop) using existing reconciler logic
                            db_row = conn.execute(f"SELECT stop_loss, target_2, shares FROM {config.E1_POSITIONS_TABLE} WHERE id = ?", [pos_id]).fetchone()
                            if db_row and db_row[0] and db_row[1]:
                                heal_stop, heal_t2, heal_shares = db_row
                                
                                # 1. Cancel all open sell orders for this symbol
                                from alpaca.trading.enums import OrderSide
                                sym_orders = [o for o in all_orders if o.symbol == ticker and o.side == OrderSide.SELL]
                                for o in sym_orders:
                                    try: client.cancel_order_by_id(o.id)
                                    except: pass
                                
                                # 2. Submit new OCO
                                from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
                                from alpaca.trading.enums import OrderClass, TimeInForce
                                client.submit_order(LimitOrderRequest(
                                    symbol=ticker, qty=int(heal_shares), side=OrderSide.SELL,
                                    limit_price=round(heal_t2, 2), time_in_force=TimeInForce.GTC,
                                    order_class=OrderClass.OCO,
                                    take_profit=TakeProfitRequest(limit_price=round(heal_t2, 2)),
                                    stop_loss=StopLossRequest(stop_price=round(heal_stop, 2))
                                ))
                                logger.info(f"      ✓ Price alignment HEAL complete for {ticker}")
                                break # Exit ticker loop after healing to avoid re-triggering on stale all_orders
                            else:
                                logger.warning(f"      [SKIP] {ticker}: Cannot heal price mismatch (Missing DB targets).")
                        except Exception as e:
                            logger.error(f"      FAILED to heal price mismatch for {ticker}: {e}")
                    
                    divergences += 1
    
    if divergences == 0:
        logger.info("  Stop state consistency: ALL CLEAR")
    else:
        logger.warning(f"  Stop state consistency: {divergences} divergence(s) logged.")

def backfill_decay_exit_verdicts(conn, today_dt):
    """
    Looks up price history for score decay exits after 20 trading days.
    """
    # Query all rows where verdict is NULL
    pending = conn.execute(f"""
        SELECT dt.id, dt.ticker, dt.exit_date, dt.exit_price, p.shares
        FROM {config.E1_DECAY_TRACKING_TABLE} dt
        LEFT JOIN {config.E1_POSITIONS_TABLE} p ON dt.position_id = p.id
        WHERE dt.verdict IS NULL
    """).fetchall()
    
    for row in pending:
        row_id, ticker, exit_date, exit_price, shares = row
        shares = shares if shares is not None else 100
        
        # Pull the prices at 5, 10, 20 trading days post-exit
        prices = conn.execute(
            "SELECT date, close FROM refined.price_history WHERE ticker = ? AND date > ? ORDER BY date ASC LIMIT 20",
            [ticker, exit_date]
        ).fetchall()
        
        if len(prices) < 20:
            logger.debug(
                f"Skipping decay backfill for {ticker} (exit {exit_date}): "
                f"only {len(prices)} post-exit price rows available (need 20). "
                f"Possible delisting or data gap."
            )
            continue
        
        def safe_price(prices, idx):
            return prices[idx][1] if len(prices) > idx else None

        # 0-indexed: index 4 is 5th day, 9 is 10th, 19 is 20th
        p5 = safe_price(prices, 4)
        p10 = safe_price(prices, 9)
        p20 = safe_price(prices, 19)
        
        if p5 is None or p10 is None or p20 is None:
            logger.warning(f"Incomplete price series for {ticker} after {exit_date}. Skipping verdict.")
            continue
        
        # Compute counterfactuals
        c5 = (p5 - exit_price) / exit_price
        c10 = (p10 - exit_price) / exit_price
        c20 = (p20 - exit_price) / exit_price
        
        pnl_counterfactual_dollars = (p20 - exit_price) * shares
        
        if p20 <= exit_price:
            verdict = 'VETO_SAVED_CAPITAL'
        elif pnl_counterfactual_dollars > 100:
            verdict = 'VETO_COST_ALPHA'        # material alpha loss
        else:
            verdict = 'VETO_COST_ALPHA_NOISE'  # technically positive but immaterial
        
        conn.execute(f"""
            UPDATE {config.E1_DECAY_TRACKING_TABLE}
            SET price_5d_post = ?, price_10d_post = ?, price_20d_post = ?,
                pnl_5d_counterfactual = ?, pnl_10d_counterfactual = ?, pnl_20d_counterfactual = ?,
                verdict = ?, verdict_date = ?
            WHERE id = ?
        """, [p5, p10, p20, c5, c10, c20, verdict, today_dt, row_id])
        
        logger.info(f"  ✓ Backfilled verdict for {ticker} (Exit: {exit_date}): {verdict}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy E1 EOD Reconciler")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode without placing orders")
    args = parser.parse_args()
    
    run_e1_reconciler(simulate=args.simulate)
