import os
import sys
import logging
from datetime import datetime, date
import duckdb
import pandas as pd

# Ensure the root of the project is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from E1.core import notifier, config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# DB_PATH removed, using config.DB_PATH
AUDIT_TABLE = 'sandbox.e1_performance_audit'

MONITOR_WINDOW = 60
MONITOR_MIN_TRADES = 20

def run_performance_monitor(simulate=False, sim_date=None, conn=None):
    logger.info("=" * 60)
    logger.info("  STRATEGY E1: DAILY PERFORMANCE & ALPHA MONITOR")
    logger.info("=" * 60)

    _owns_conn = conn is None
    con = conn if conn is not None else duckdb.connect(config.DB_PATH)
    
    # Ensure Audit Table exists
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
            audit_date DATE PRIMARY KEY,
            t2_hit_rate FLOAT,
            be_stop_ratio FLOAT,
            time_exit_avg_pnl FLOAT,
            status VARCHAR,
            flags VARCHAR,
            window_size INTEGER
        )
    """)
    # Schema Migration: Add window_size if it doesn't exist (F-04 recovery)
    try:
        con.execute(f"ALTER TABLE {AUDIT_TABLE} ADD COLUMN window_size INTEGER")
    except Exception:
        pass # Already exists

    # 1. Fetch Last N HEALTHY Closed Trades
    try:
        # F-17: Scope closed trades query to sim_date
        query = f"""
        SELECT 
            ticker, 
            exit_trigger, 
            stop_stage, 
            pnl_dollars,
            exit_date
        FROM {config.E1_POSITIONS_TABLE}
        WHERE status = 'CLOSED' 
          AND entry_regime = 'HEALTHY'
          AND exit_trigger != 'ALPACA_SYNC_DESYNC'
          AND (? IS NULL OR exit_date <= ?)
          AND exit_date >= CAST(COALESCE(?, CURRENT_DATE) AS DATE) - INTERVAL 180 DAYS
        ORDER BY exit_date DESC
        LIMIT {MONITOR_WINDOW}
        """
        sim_date_str = sim_date.isoformat() if sim_date else None
        df = con.execute(query, [sim_date_str, sim_date_str, sim_date_str]).df()
        
        if len(df) < MONITOR_MIN_TRADES:
            logger.info(f"Insufficient trade history ({len(df)}/{MONITOR_MIN_TRADES}) for rolling audit. Skipping.")
            return

        # 2. Compute Metrics
        total_trades = len(df)
        window_size = total_trades
        
        # F-05: T2 Detection - Prefix Match
        T2_TRIGGER_PREFIX = 'Target 2'
        t2_hits = len(df[df['exit_trigger'].str.startswith(T2_TRIGGER_PREFIX, na=False)])
        t2_hit_rate = (t2_hits / total_trades) * 100

        # F-05: Stop Detection - Case-insensitive match (catches STOP_VIOLATION)
        stops = df[df['exit_trigger'].str.contains('stop', case=False, na=False)]
        if len(stops) > 0:
            be_stops = len(stops[stops['stop_stage'] == 'BREAKEVEN'])
            be_stop_ratio = (be_stops / len(stops)) * 100
        else:
            be_stop_ratio = 100.0 # No stops hit is healthy

        time_exits = df[df['exit_trigger'] == 'TIME_EXIT_20D']
        time_exit_avg = time_exits['pnl_dollars'].mean() if len(time_exits) > 0 else 0.0

        logger.info(f"Monitor raw counts: t2_hits={t2_hits}, stops={len(stops)}, time_exits={len(time_exits)}")

        # 3. Evaluate Thresholds (Spec §6.1)
        flags = []
        status = "HEALTHY"

        if t2_hit_rate < 10.0:
            status = "WARNING"
            flags.append(f"T2 Hit Rate ({t2_hit_rate:.1f}%) < 10% Floor")
        
        if be_stop_ratio < 50.0:
            status = "CRITICAL"
            flags.append(f"BE Stop Ratio ({be_stop_ratio:.1f}%) < 50% Floor")

        if time_exit_avg < -50.0:
            if status == 'HEALTHY':
                status = 'WARNING'
            flags.append(f"Time Exit Drift (${time_exit_avg:.2f}) < -$50 Decay")

        # F-17: Use sim_date for audit date
        reference_date = sim_date if sim_date else date.today()
        today_str = reference_date.isoformat()

        # F-09: Consecutive-Window Rule
        prev_row = con.execute(f"""
            SELECT status FROM {AUDIT_TABLE}
            WHERE audit_date < ?
            ORDER BY audit_date DESC LIMIT 1
        """, [today_str]).fetchone()
        prev_status = prev_row[0] if prev_row else 'HEALTHY'

        escalated_telegram_msg = None
        if status == 'WARNING' and prev_status == 'WARNING':
            status = 'CRITICAL'
            flags.append("CONSECUTIVE WARNING — Full audit required")
            escalated_telegram_msg = (
                "🚨 <b>E1 CONSECUTIVE WARNING — AUDIT REQUIRED</b>\n"
                "T2 Hit Rate has been below 10% for two consecutive 30-session "
                "windows. Per Spec §6.1: Full weight and threshold audit is mandatory "
                "before next session.\n\n"
            )
        elif status == 'CRITICAL' and prev_status == 'CRITICAL':
            flags.append("CONSECUTIVE CRITICAL — Full audit required")
            escalated_telegram_msg = (
                "🚨 <b>E1 CONSECUTIVE CRITICAL — AUDIT REQUIRED</b>\n"
                "BE Stop Ratio has been below 50% for two consecutive 30-session "
                "windows. Per Spec §6.1: Full weight and threshold audit is mandatory "
                "before next session.\n\n"
            )

        flag_str = "; ".join(flags) if flags else "No Violations"

        # 4. Log to DB
        if not simulate:
            con.execute(f"""
                INSERT INTO {AUDIT_TABLE} (audit_date, t2_hit_rate, be_stop_ratio, time_exit_avg_pnl, status, flags, window_size)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (audit_date) DO UPDATE SET
                    t2_hit_rate = EXCLUDED.t2_hit_rate,
                    be_stop_ratio = EXCLUDED.be_stop_ratio,
                    time_exit_avg_pnl = EXCLUDED.time_exit_avg_pnl,
                    status = EXCLUDED.status,
                    flags = EXCLUDED.flags,
                    window_size = EXCLUDED.window_size
            """, [today_str, t2_hit_rate, be_stop_ratio, time_exit_avg, status, flag_str, window_size])

        # 5. Telegram Notification
        emoji = "✅" if status == "HEALTHY" else "⚠️" if status == "WARNING" else "🚨"
        msg = escalated_telegram_msg if escalated_telegram_msg else ""
        msg += (
            f"{emoji} <b>E1 PERFORMANCE AUDIT</b>\n"
            f"<i>Rolling {window_size}-Session (HEALTHY)</i>\n\n"
            f"<b>Status</b>: {status}\n"
            f"<b>T2 Hit Rate</b>: {t2_hit_rate:.1f}% (Target 15%)\n"
            f"<b>BE Stop Ratio</b>: {be_stop_ratio:.1f}% (Target 75%)\n"
            f"<b>Time Exit Avg</b>: ${time_exit_avg:.2f}\n\n"
            f"<b>Flags</b>: {flag_str}\n\n"
        )
        if time_exit_avg < -50.0:
            msg += f"⚠️ *Time Exit Drift*: {time_exit_avg:.2f} (below -$50 floor)\n\n"
            
        msg += f"<i>Ref: E1_SPECIFICATION §6.1</i>"
        
        if not simulate:
            notifier.send_telegram(msg)
        else:
            logger.info(f"[SIMULATE] Would send Telegram audit summary: Status {status}")
        logger.info(f"Audit Complete. Status: {status}")

    except Exception as e:
        logger.error(f"Performance Monitor failed: {e}")
    finally:
        if _owns_conn:
            con.close()

if __name__ == "__main__":
    run_performance_monitor()
