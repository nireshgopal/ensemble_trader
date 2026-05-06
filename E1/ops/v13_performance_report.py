import pandas as pd
import duckdb
from datetime import datetime
import sys
import os
# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import E1.core.config as config

# Paths
EQUITY_CURVE_PATH = r"refine/backtest/results/v3_final_2014_2026/equity_curve.csv"
TRADES_PATH = r"refine/backtest/results/v3_final_2014_2026/trades.csv"
# DB_PATH removed, using config.DB_PATH

def calculate_performance():
    # 1. Load Strategy Data
    equity_df = pd.read_csv(EQUITY_CURVE_PATH)
    equity_df['date'] = pd.to_datetime(equity_df['date'])
    equity_df['year'] = equity_df['date'].dt.year
    
    # Get annual returns for strategy
    annual_equity = equity_df.groupby('year').last()['equity']
    initial_equity = 100000.0
    
    strategy_returns = {}
    prev_val = initial_equity
    for year, val in annual_equity.items():
        strategy_returns[year] = (val - prev_val) / prev_val
        prev_val = val
        
    # 2. Load Trade Data
    trades_df = pd.read_csv(TRADES_PATH)
    trades_df['exit_date'] = pd.to_datetime(trades_df['exit_date'])
    trades_df['year'] = trades_df['exit_date'].dt.year
    
    annual_trades = trades_df.groupby('year').size()
    annual_wins = trades_df[trades_df['pnl_pct'] > 0].groupby('year').size()
    annual_win_rate = (annual_wins / annual_trades).fillna(0)
    
    # 3. Load Benchmark Data from DuckDB
    con = duckdb.connect(config.DB_PATH, read_only=True)
    # Get last price of each year for benchmarks
    bench_annual = con.execute("""
        WITH YearlyLast AS (
            SELECT ticker, year(date) as yr, close,
                   ROW_NUMBER() OVER (PARTITION BY ticker, year(date) ORDER BY date DESC) as rn
            FROM refined.price_history
            WHERE ticker IN ('SPY', 'DIA')
            AND date >= '2013-12-01'
        )
        SELECT yr, ticker, close FROM YearlyLast WHERE rn = 1
    """).df()
    
    # Also get first price of 2014 to anchor the 2014 return
    anchor_prices = con.execute("""
        SELECT ticker, close as anchor_price
        FROM refined.price_history
        WHERE ticker IN ('SPY', 'DIA')
        AND date >= '2014-01-01'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date ASC) = 1
    """).df().set_index('ticker')['anchor_price'].to_dict()
    con.close()
    
    bench_returns = {'SPY': {}, 'DIA': {}}
    for ticker in ['SPY', 'DIA']:
        ticker_data = bench_annual[bench_annual['ticker'] == ticker].sort_values('yr')
        prev_val = anchor_prices.get(ticker, 0)
        for _, row in ticker_data.iterrows():
            yr = row['yr']
            if yr < 2014: continue
            cur_val = row['close']
            bench_returns[ticker][yr] = (cur_val - prev_val) / prev_val
            prev_val = cur_val

    # 4. Consolidate
    years = sorted(strategy_returns.keys())
    data = []
    for yr in years:
        data.append({
            'Year': yr,
            'E1 V1.3': f"{strategy_returns.get(yr, 0):.1%}",
            'Trades': int(annual_trades.get(yr, 0)),
            'Win Rate': f"{annual_win_rate.get(yr, 0):.1%}",
            'SPY': f"{bench_returns['SPY'].get(yr, 0):.1%}",
            'DIA (DOW)': f"{bench_returns['DIA'].get(yr, 0):.1%}"
        })
        
    report_df = pd.DataFrame(data)
    print(report_df.to_string(index=False))
    
    # Calculate Total CAGR
    last_val = equity_df['equity'].iloc[-1]
    years_elapsed = (equity_df['date'].iloc[-1] - equity_df['date'].iloc[0]).days / 365.25
    cagr = (last_val / initial_equity) ** (1 / years_elapsed) - 1
    
    print(f"\n**Total Period CAGR (2014-2026): {cagr:.2%}**")
    print(f"**Total Trades: {len(trades_df)}**")
    print(f"**Total Win Rate: {(len(trades_df[trades_df['pnl_pct'] > 0]) / len(trades_df)):.1%}**")

if __name__ == "__main__":
    calculate_performance()
