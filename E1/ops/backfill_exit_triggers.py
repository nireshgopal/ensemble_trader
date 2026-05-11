import duckdb
import pandas as pd
import logging
from E1.core import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def backfill_triggers():
    conn = duckdb.connect(config.DB_PATH)
    
    logger.info("Starting historical exit trigger backfill...")
    
    # 1. Update E1_POSITIONS_TABLE
    # Priority 1: Time Exit (held >= 20 days and status=CLOSED)
    res_time = conn.execute(f"""
        UPDATE {config.E1_POSITIONS_TABLE}
        SET exit_trigger = 'TIME_EXIT_20D'
        WHERE status = 'CLOSED' 
          AND days_held >= 19
          AND (exit_trigger IS NULL OR exit_trigger = 'SELL' OR exit_trigger = 'CLOSE_POSITION')
    """)
    logger.info(f"Updated {res_time.rowcount} TIME_EXIT_20D positions.")
    
    # Priority 2: Stop Violation (price near stop_loss)
    res_stop = conn.execute(f"""
        UPDATE {config.E1_POSITIONS_TABLE}
        SET exit_trigger = 'STOP_VIOLATION'
        WHERE status = 'CLOSED'
          AND (exit_trigger IS NULL OR exit_trigger = 'SELL' OR exit_trigger = 'CLOSE_POSITION')
          AND ABS(exit_price - stop_loss) < 0.05
    """)
    logger.info(f"Updated {res_stop.rowcount} STOP_VIOLATION positions.")
    
    # Priority 3: Target 2 Hit (price near target_2)
    res_t2 = conn.execute(f"""
        UPDATE {config.E1_POSITIONS_TABLE}
        SET exit_trigger = 'TARGET_2_HIT'
        WHERE status = 'CLOSED'
          AND (exit_trigger IS NULL OR exit_trigger = 'SELL' OR exit_trigger = 'CLOSE_POSITION')
          AND ABS(exit_price - target_2) < 0.05
    """)
    logger.info(f"Updated {res_t2.rowcount} TARGET_2_HIT positions.")
    
    # Priority 4: Score Decay (marked as SELL in audit reports but need label)
    # We can't perfectly re-derive this without re-running, so we mark as UNKNOWN_LEGACY 
    # if it doesn't fit the above clear buckets.
    res_legacy = conn.execute(f"""
        UPDATE {config.E1_POSITIONS_TABLE}
        SET exit_trigger = 'UNKNOWN_LEGACY'
        WHERE status = 'CLOSED'
          AND (exit_trigger IS NULL OR exit_trigger = 'SELL' OR exit_trigger = 'CLOSE_POSITION')
    """)
    logger.info(f"Marked {res_legacy.rowcount} remaining legacy exits as UNKNOWN_LEGACY.")
    
    # 2. Sync to Trade Log
    res_sync = conn.execute(f"""
        UPDATE {config.E1_TRADE_LOG_TABLE} tl
        SET trigger = p.exit_trigger
        FROM {config.E1_POSITIONS_TABLE} p
        WHERE tl.position_id = p.id
          AND tl.action = 'EXIT'
          AND (tl.trigger IS NULL OR tl.trigger IN ('SELL', 'CLOSE_POSITION', 'UNKNOWN'))
    """)
    logger.info(f"Synchronized {res_sync.rowcount} trade log entries.")
    
    conn.close()
    logger.info("Backfill complete.")

if __name__ == "__main__":
    backfill_triggers()
