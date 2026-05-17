"""
E1/pipeline/log_ensemble_scores.py

Daily script — computes and stores ticker-level ensemble scores into
refined.ensemble_daily_scores. Run AFTER build_ml_dataset.py.

BEAR regime filters (provisional):
  - Drawdown ≤ -65% → hard veto (ensemble_score = 0.0)
  - Piotroski F ≤ 4  → hard veto (ensemble_score = 0.0)
  Both logged via bear_dd_veto / bear_pio_veto columns for transparency.
"""

import duckdb
import pandas as pd
import os
import sys
import datetime
from E1.core.config import DB_PATH

sys.path.insert(0, os.getcwd())
from E1.core.signal_votes import (
    load_regime_weights, compute_votes, aggregate_score, compute_dominant_cluster, CANONICAL_SIGNALS
)
from E1.core import config

WEIGHTS_JSON_PATH = os.path.join('docs', 'signal_weights.json')
IC_SUMMARY_PATH = os.path.join('docs', 'ic_summary.csv')

# Sectors where Piotroski F-Score is structurally inapplicable.
PIOTROSKI_EXEMPT_SECTORS = {
    'Financial Services',
    'Financials',
    'ETF',
    'Exchange Traded Fund',
    'Real Estate',
    None,
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS refined.ensemble_daily_scores (
    date DATE,
    ticker VARCHAR,
    sector VARCHAR,
    regime VARCHAR,
    weights_regime VARCHAR,
    ensemble_score DOUBLE,
    sig_rs_3month DOUBLE,
    sig_ma_slope DOUBLE,
    sig_rsi_oversold DOUBLE,
    sig_drawdown_recovery DOUBLE,
    sig_fundamental DOUBLE,
    sig_rs_12month DOUBLE,
    sig_rs_6month DOUBLE,
    sig_price_stage DOUBLE,
    sig_52w_high DOUBLE,
    sig_volume DOUBLE,
    close_price DOUBLE,
    drawdown_52w DOUBLE,
    piotroski_f_score INTEGER,
    short_float_pct DOUBLE,
    bear_dd_veto BOOLEAN DEFAULT FALSE,
    bear_pio_veto BOOLEAN DEFAULT FALSE,
    rationale VARCHAR,
    dominant_cluster VARCHAR,
    cluster_dominance_pct DOUBLE,
    PRIMARY KEY (date, ticker)
)
"""

_NEW_COLUMNS = [
    ("drawdown_52w", "DOUBLE"),
    ("piotroski_f_score", "INTEGER"),
    ("short_float_pct", "DOUBLE"),
    ("bear_dd_veto", "BOOLEAN DEFAULT FALSE"),
    ("bear_pio_veto", "BOOLEAN DEFAULT FALSE"),
    ("rationale", "VARCHAR"),
    ("dominant_cluster", "VARCHAR"),
    ("cluster_dominance_pct", "DOUBLE"),
    ("sig_rs_12month", "DOUBLE"),
    ("sig_rs_6month", "DOUBLE"),
    ("sig_price_stage", "DOUBLE"),
    ("sig_52w_high", "DOUBLE"),
    ("sig_volume", "DOUBLE"),
]

def _ensure_columns(con):
    existing_cols = [r[1] for r in con.execute("PRAGMA table_info('refined.ensemble_daily_scores')").fetchall()]
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing_cols:
            try:
                con.execute(f"ALTER TABLE refined.ensemble_daily_scores ADD COLUMN {col_name} {col_type}")
                print(f"  [SCHEMA] Added column: {col_name} {col_type}")
            except Exception as e:
                print(f"  [WARN] Failed to add column {col_name}: {e}")

def _load_piotroski_map(con, target_date):
    try:
        df = con.execute("""
            SELECT f.ticker,
                   COALESCE(f.piotroski_f_score_live, f.piotroski_f_score)
                       AS piotroski_f_score
            FROM refined.financials f
            WHERE COALESCE(f.piotroski_f_score_live, f.piotroski_f_score) IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY f.ticker
                ORDER BY f.report_date DESC
            ) = 1
        """).df()
        return dict(zip(df['ticker'], df['piotroski_f_score'].astype(int)))
    except Exception as e:
        print(f"  [WARN] Failed to load Piotroski data: {e}")
        return {}

def _generate_rationale(row, votes, score, regime, pio_score):
    short_float = row.get('short_float_pct')
    if short_float is not None and short_float > 0.15:
        if row.get('sector') not in PIOTROSKI_EXEMPT_SECTORS:
            return f"VETO: High Short Float ({short_float:.1%})"

    bear_dd_threshold = getattr(config, 'BEAR_DRAWDOWN_VETO', -0.65)
    bear_pio_threshold = getattr(config, 'BEAR_PIOTROSKI_VETO', 4)

    dd = row.get('drawdown_52w')
    if regime == 'BEAR':
        if dd is not None and dd <= bear_dd_threshold:
            return f"VETO: Excessive Drawdown ({dd:.1%})"
        if pio_score is not None and pio_score <= bear_pio_threshold:
            return f"VETO: Weak Fundamentals (Piotroski {pio_score})"
        
    rsi = row.get('rsi_14')
    if rsi is not None and rsi > 75:
        return f"AVOID: Overextended (RSI {rsi:.1f})"

    sector = row.get('sector', 'Unknown')
    industry = row.get('industry', '')
    desc = f"{sector} {industry}".strip()

    if regime == 'BEAR':
        if votes.get('sig_rsi_oversold', 0) > 0.5:
            base = f"Oversold {desc} (RSI {rsi:.1f})"
        elif votes.get('sig_drawdown_recovery', 0) > 0.5:
            base = f"Deep Recovery {desc} (DD {dd:.1%})"
        else:
            base = f"Neutral {desc}"
        if pio_score is not None:
            base += f" - Quality F{pio_score}"
        return base

    if score >= 0.65:
        return f"Strong Trend {desc}"
    elif score <= 0.35:
        return f"Degrading Trend {desc}"
    else:
        return f"Neutral {desc}"
def run(target_date=None, con=None, cached_weights=None, **kwargs):
    external_con = con is not None
    if not external_con:
        con = duckdb.connect(DB_PATH)

    if target_date is None:
        target_date = con.execute("SELECT MAX(date) FROM refined.daily_signals_ml").fetchone()[0]

    con.execute("CREATE SCHEMA IF NOT EXISTS refined")
    con.execute(CREATE_TABLE_SQL)
    _ensure_columns(con)

    rw = cached_weights if cached_weights is not None else load_regime_weights(WEIGHTS_JSON_PATH)

    # Optimization: Allow passing pre-cached maps
    pio_map = kwargs.get('pio_map')
    if pio_map is None:
        # For historical rebuilds, we often skip this or use a dummy to avoid 2000+ queries
        pio_map = {}
        
    analyst_map = kwargs.get('analyst_map')
    if analyst_map is None:
        analyst_df = con.execute("""
            SELECT ticker, short_percent_of_float
            FROM yahoo.analyst_data
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY fetched_at DESC) = 1
        """).df()
        analyst_map = dict(zip(analyst_df['ticker'], analyst_df['short_percent_of_float']))

    con.execute("DELETE FROM refined.ensemble_daily_scores WHERE date = ?", [target_date])

    df = con.execute("""
        SELECT 
            s.*,
            ns.final_sentiment_factor
        FROM refined.daily_signals_ml s
        LEFT JOIN (
            SELECT ticker, final_sentiment_factor
            FROM refined.daily_sentiment
            WHERE calc_date >= CAST(? AS DATE) - INTERVAL 7 DAY
              AND calc_date <= CAST(? AS DATE)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY calc_date DESC) = 1
        ) ns ON s.ticker = ns.ticker
        WHERE s.date = ? AND s.close_price > 1.0
    """, [target_date, target_date, target_date]).df()

    if df.empty:
        if not external_con: con.close()
        return

    # Regime override
    live_regime = kwargs.get('live_regime_override')
    if live_regime is None:
        regime_row = con.execute("SELECT regime FROM refined.market_regime WHERE date = ?", [target_date]).fetchone()
        live_regime = regime_row[0] if (regime_row and regime_row[0]) else None

    results = []
    bear_dd_threshold = getattr(config, 'BEAR_DRAWDOWN_VETO', -0.65)
    bear_pio_threshold = getattr(config, 'BEAR_PIOTROSKI_VETO', 4)

    for _, row in df.iterrows():
        r = row.to_dict()
        b_pct = r.get('breadth_pct', 0.6)
        if live_regime: r['regime'] = live_regime

        votes = compute_votes(r, breadth_pct=b_pct)
        score = aggregate_score(votes, rw, row=r)

        regime = live_regime if live_regime else (r.get('regime') or 'SAFE_DEFAULT')
        weights_regime = regime if regime in rw else 'SAFE_DEFAULT'
        
        res = rw.get(weights_regime, rw.get('SAFE_DEFAULT'))
        active_w = res[0]
        cluster, dominance = compute_dominant_cluster(votes, active_w)

        ticker = r['ticker']
        drawdown = r.get('drawdown_52w')
        pio_score = pio_map.get(ticker)
        short_float = analyst_map.get(ticker)

        short_float_veto = False
        if short_float is not None and short_float > 0.15:
            if r.get('sector') not in PIOTROSKI_EXEMPT_SECTORS:
                short_float_veto = True

        bear_dd_veto = False
        bear_pio_veto = False
        if regime == 'BEAR':
            if drawdown is not None and drawdown <= bear_dd_threshold:
                bear_dd_veto = True
            ticker_sector = r.get('sector')
            is_pio_exempt = ticker_sector in PIOTROSKI_EXEMPT_SECTORS or ticker_sector is None
            if is_pio_exempt:
                if pio_score is not None and pio_score <= bear_pio_threshold:
                    bear_pio_veto = True
            else:
                if pio_score is None or pio_score <= bear_pio_threshold:
                    bear_pio_veto = True

        if bear_dd_veto or bear_pio_veto or short_float_veto:
            score = 0.0

        rationale = _generate_rationale(r, votes, score, regime, pio_score)

        results.append({
            'date': r['date'], 'ticker': ticker, 'sector': r.get('sector'),
            'regime': regime, 'weights_regime': weights_regime, 'ensemble_score': round(score, 4),
            'sig_rs_3month': round(votes.get('sig_rs_3month', 0), 4) if votes.get('sig_rs_3month') is not None else None,
            'sig_ma_slope': round(votes.get('sig_ma_slope', 0), 4) if votes.get('sig_ma_slope') is not None else None,
            'sig_rsi_oversold': round(votes.get('sig_rsi_oversold', 0), 4) if votes.get('sig_rsi_oversold') is not None else None,
            'sig_drawdown_recovery': round(votes.get('sig_drawdown_recovery', 0), 4) if votes.get('sig_drawdown_recovery') is not None else None,
            'sig_fundamental': votes.get('sig_fundamental'),
            'sig_rs_12month': round(votes.get('sig_rs_12month', 0), 4) if votes.get('sig_rs_12month') is not None else None,
            'sig_rs_6month': round(votes.get('sig_rs_6month', 0), 4) if votes.get('sig_rs_6month') is not None else None,
            'sig_price_stage': round(votes.get('sig_price_stage', 0), 4) if votes.get('sig_price_stage') is not None else None,
            'sig_52w_high': round(votes.get('sig_52w_high', 0), 4) if votes.get('sig_52w_high') is not None else None,
            'sig_volume': round(votes.get('sig_volume', 0), 4) if votes.get('sig_volume') is not None else None,
            'close_price': r.get('close_price'), 'drawdown_52w': drawdown,
            'piotroski_f_score': pio_score, 'short_float_pct': short_float,
            'bear_dd_veto': bear_dd_veto, 'bear_pio_veto': bear_pio_veto,
            'rationale': rationale, 'dominant_cluster': cluster, 'cluster_dominance_pct': round(dominance, 4)
        })

    if results:
        result_df = pd.DataFrame(results)
        con.register("temp_scores", result_df)
        cols = [
            "date", "ticker", "sector", "regime", "weights_regime", "ensemble_score",
            "sig_rs_3month", "sig_ma_slope",
            "sig_rsi_oversold", "sig_drawdown_recovery", "sig_fundamental",
            "sig_rs_12month", "sig_rs_6month", "sig_price_stage", "sig_52w_high", "sig_volume",
            "close_price", "drawdown_52w", "piotroski_f_score", "short_float_pct",
            "bear_dd_veto", "bear_pio_veto", "rationale", "dominant_cluster", "cluster_dominance_pct"
        ]
        col_str = ", ".join(cols)
        con.execute(f"INSERT INTO refined.ensemble_daily_scores ({col_str}) SELECT {col_str} FROM temp_scores")
    
    if not external_con:
        con.close()
        print(f"  Done {target_date}.", flush=True)

def rebuild_all(start_date=None, end_date=None):
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA IF NOT EXISTS refined")
    con.execute(CREATE_TABLE_SQL)
    _ensure_columns(con)
    
    query = "SELECT DISTINCT date FROM refined.daily_signals_ml"
    conditions = []
    params = []
    if start_date:
        conditions.append("date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("date <= ?")
        params.append(end_date)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY date"
    dates_df = con.execute(query, params).df()
    dates = dates_df['date'].tolist()
    print(f"Starting Optimized Rebuild: {start_date or 'BEGINNING'} to {end_date or 'LATEST'} ({len(dates)} dates)", flush=True)
    
    rw = load_regime_weights(WEIGHTS_JSON_PATH)
    
    print("Pre-caching market regimes...", flush=True)
    regimes = con.execute("SELECT date, regime FROM refined.market_regime").df()
    regime_map = dict(zip(regimes['date'], regimes['regime']))
    
    print("Pre-caching analyst data...", flush=True)
    analyst_df = con.execute("""
        SELECT ticker, short_percent_of_float
        FROM yahoo.analyst_data
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY fetched_at DESC) = 1
    """).df()
    analyst_map = dict(zip(analyst_df['ticker'], analyst_df['short_percent_of_float']))
    
    # We skip the global pio_map pre-cache as it's too slow. 
    # run() will fetch its own if not provided, or we can provide a dummy for rebuild.
    # For Phase 5 IC study, we only need the signals, the vetoes (Piotroski) are secondary.
    
    for i, d in enumerate(dates):
        # Pass regime from map to avoid query in run()
        live_regime = regime_map.get(d)
        run(d, con=con, cached_weights=rw, analyst_map=analyst_map, live_regime_override=live_regime)
        
        if (i + 1) % 50 == 0 or (i + 1) == len(dates):
            print(f"  Progress: {i+1}/{len(dates)} dates completed.", flush=True)

    con.close()
    print("Rebuild Complete.", flush=True)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str)
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--start', type=str)
    parser.add_argument('--end', type=str)
    args = parser.parse_args()
    if args.rebuild:
        rebuild_all(start_date=args.start, end_date=args.end)
    else:
        run(args.date)
