import duckdb
import pandas as pd
import argparse
import sys
import os
import time
from E1.core.config import DB_PATH

# Signals to track from ensemble_daily_scores
SIGNAL_COLS = [
    ('ensemble_score', 'score'),
    ('sig_rs_3month', 'rs_3m'),
    ('sig_ma_slope', 'ma_slope'),
    ('sig_rsi_oversold', 'rsi_bounce'),
    ('sig_drawdown_recovery', 'dd_recovery'),
    ('sig_fundamental', 'pead')
]

HORIZONS = [1, 2, 3, 5, 21]

def build_momentum_query(mode="snapshot", target_date=None, limit_tickers=None):
    where_clause = ""
    if mode == "snapshot":
        if target_date:
            where_clause = f"WHERE s.date = '{target_date}'"
        else:
            where_clause = f"WHERE s.date = (SELECT MAX(date) FROM refined.ensemble_daily_scores)"
            
    if limit_tickers:
        ticker_list = ", ".join([f"'{t}'" for t in limit_tickers])
        if where_clause:
            where_clause += f" AND s.ticker IN ({ticker_list})"
        else:
            where_clause = f"WHERE s.ticker IN ({ticker_list})"

    signal_deltas = []
    for col_name, row_prefix in SIGNAL_COLS:
        for h in HORIZONS:
            label = f"{h}d"
            signal_deltas.append(f"{col_name} - LAG({col_name}, {h}) OVER (PARTITION BY ticker ORDER BY date) as {row_prefix}_{label}")

    price_deltas = []
    for h in HORIZONS:
        label = f"{h}d"
        price_deltas.append(f"CASE WHEN LAG(close, {h}) OVER (PARTITION BY ticker ORDER BY date) > 0 THEN (close - LAG(close, {h}) OVER (PARTITION BY ticker ORDER BY date)) / LAG(close, {h}) OVER (PARTITION BY ticker ORDER BY date) ELSE NULL END as price_{label}")

    score_cols = [f"s.score_{h}d" for h in HORIZONS]
    price_cols = [f"p.price_{h}d" for h in HORIZONS]
    
    other_signal_cols = []
    for _, prefix in SIGNAL_COLS[1:]:
        for h in HORIZONS:
            other_signal_cols.append(f"s.{prefix}_{h}d")

    query = f"""
        WITH signal_lags AS (
            SELECT date, ticker, {", ".join(signal_deltas)}
            FROM refined.ensemble_daily_scores
        ),
        price_lags AS (
            SELECT date, ticker, {", ".join(price_deltas)}
            FROM refined.price_history
        )
        SELECT 
            s.date, 
            s.ticker, 
            {", ".join(score_cols)},
            {", ".join(price_cols)},
            {", ".join(other_signal_cols)}
        FROM signal_lags s
        LEFT JOIN price_lags p ON s.ticker = p.ticker AND s.date = p.date
        {where_clause}
    """
    return query

def get_audit_columns():
    cols = ['date', 'ticker']
    for h in HORIZONS: cols.append(f"score_{h}d")
    for h in HORIZONS: cols.append(f"price_{h}d")
    for _, prefix in SIGNAL_COLS[1:]:
        for h in HORIZONS:
            cols.append(f"{prefix}_{h}d")
    return cols

def execute_backfill(con):
    print("Starting Full Universe Momentum Backfill...")
    start_time = time.time()
    cols = get_audit_columns()
    query = build_momentum_query(mode="backfill")
    con.execute(f"INSERT OR REPLACE INTO refined.e1_momentum_audit ({', '.join(cols)}) {query}")
    elapsed = time.time() - start_time
    print(f"Backfill completed in {elapsed:.2f} seconds.")

def execute_snapshot(con, target_date=None, open_only=False):
    limit_tickers = None
    if open_only:
        open_tickers = con.execute("SELECT ticker FROM refined.e1_positions WHERE status = 'OPEN'").df()['ticker'].tolist()
        if not open_tickers:
            print("No open positions found. Skipping snapshot.")
            return
        limit_tickers = open_tickers

    print(f"Executing Momentum Snapshot for {target_date if target_date else 'Latest Date'}...")
    cols = get_audit_columns()
    query = build_momentum_query(mode="snapshot", target_date=target_date, limit_tickers=limit_tickers)
    con.execute(f"INSERT OR REPLACE INTO refined.e1_momentum_audit ({', '.join(cols)}) {query}")
    print("Snapshot completed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--date", type=str)
    parser.add_argument("--open-only", action="store_true")
    args = parser.parse_args()

    con = duckdb.connect(DB_PATH)
    try:
        if args.backfill:
            execute_backfill(con)
        else:
            execute_snapshot(con, target_date=args.date, open_only=args.open_only)
    finally:
        con.close()
