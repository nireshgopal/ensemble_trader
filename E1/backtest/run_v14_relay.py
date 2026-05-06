import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import pandas as pd
import numpy as np
import duckdb
import math
import logging
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from E1.core import e1_sizer, config

# Silence noisy logs for research
logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

DB_PATH = config.DB_PATH
INITIAL_CAPITAL = 50000.0
START_DATE = '2026-01-01'
END_DATE = '2026-05-01'

@dataclass
class Trade:
    ticker: str
    entry_date: date
    entry_price: float
    exit_date: Optional[date] = None
    exit_price: float = 0.0
    shares: int = 0
    dollar_val: float = 0.0
    reason: str = ''
    pnl_pct: float = 0.0
    pnl_dollars: float = 0.0
    days_held: int = 0
    sector: str = 'Miscellaneous'

def get_sector_rs_for_date(conn, target_date_str):
    """
    Computes 3-month trailing RS for all sectors vs SPY as of a specific date.
    Ported from E1/core/e1_trader.py
    """
    query = f"""
    WITH daily_rets AS (
        SELECT date, sector, AVG((close_price - prev_close)/NULLIF(prev_close,0)) as sector_ret
        FROM (
            SELECT date, ticker, sector, close_price, LAG(close_price) OVER (PARTITION BY ticker ORDER BY date) as prev_close
            FROM refined.daily_signals_ml
            WHERE date BETWEEN CAST('{target_date_str}' AS DATE) - INTERVAL 100 DAYS AND '{target_date_str}'
        ) GROUP BY 1, 2
    ),
    mkt_rets AS (
        SELECT date, close as spy_close, (close - LAG(close) OVER (ORDER BY date))/NULLIF(LAG(close) OVER (ORDER BY date),0) as mkt_ret
        FROM refined.price_history WHERE ticker = 'SPY' AND date BETWEEN CAST('{target_date_str}' AS DATE) - INTERVAL 100 DAYS AND '{target_date_str}'
    ),
    rs_calc AS (
        SELECT 
            d.sector,
            EXP(SUM(LN(1 + sector_ret))) as s_growth,
            EXP(SUM(LN(1 + mkt_ret))) as m_growth
        FROM daily_rets d JOIN mkt_rets m ON d.date = m.date
        GROUP BY d.sector
    )
    SELECT sector, s_growth / m_growth as rs FROM rs_calc
    """
    try:
        df_rs = conn.execute(query).df()
        return dict(zip(df_rs['sector'], df_rs['rs']))
    except:
        return {}

def run_v14_relay():
    print(f"\n{'='*60}")
    print(f"  STRATEGY E1 V1.4 RELAY TEST: 2026 YTD")
    print(f"  Initial Capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"{'='*60}\n")

    conn = duckdb.connect(DB_PATH)
    
    # Load Data
    print("Loading 2026 market data...")
    df = conn.execute(f"""
        SELECT s.ticker, s.date, s.close_price, ph.high, ph.low, s.atr_14, s.sector,
               e.ensemble_score, m.regime, m.vix_close, ma.hy_spread
        FROM refined.daily_signals_ml s
        JOIN refined.price_history ph ON s.ticker = ph.ticker AND s.date = ph.date
        JOIN refined.ensemble_daily_scores e ON s.ticker = e.ticker AND s.date = e.date
        JOIN refined.market_regime m ON s.date = m.date
        JOIN refined.macro_daily ma ON s.date = ma.date
        WHERE s.date >= '{START_DATE}' AND s.date <= '{END_DATE}'
        ORDER BY s.date, e.ensemble_score DESC
    """).df()
    
    dates = sorted(df['date'].unique())
    cash = INITIAL_CAPITAL
    portfolio_equity = INITIAL_CAPITAL
    open_positions: List[Trade] = []
    closed_trades: List[Trade] = []
    equity_curve = []

    for current_date in dates:
        today_df = df[df['date'] == current_date]
        today_date = pd.Timestamp(current_date).date()
        today_str = str(today_date)
        
        # Get market context
        regime = today_df['regime'].iloc[0]
        vix = today_df['vix_close'].iloc[0]
        hy = today_df['hy_spread'].iloc[0]
        
        # Compute Sector RS for TODAY
        sector_rs_lookup = get_sector_rs_for_date(conn, today_str)

        # 1. Evaluate Exits (20-Day Time Exit Baseline)
        for pos in open_positions[:]:
            pos.days_held += 1
            ticker_row = today_df[today_df['ticker'] == pos.ticker]
            
            if ticker_row.empty: continue
            
            curr_price = float(ticker_row['close_price'].iloc[0])
            
            if pos.days_held >= 20:
                pos.exit_date = today_date
                pos.exit_price = curr_price
                pos.pnl_pct = (pos.exit_price - pos.entry_price) / pos.entry_price
                pos.pnl_dollars = pos.pnl_pct * pos.dollar_val
                pos.reason = 'TIME_EXIT'
                
                cash += pos.dollar_val + pos.pnl_dollars
                closed_trades.append(pos)
                open_positions.remove(pos)

        # 2. Evaluate Entries
        candidates = today_df[today_df['ensemble_score'] >= 0.65]
        
        # Track sector exposure for L2
        sector_mv = {}
        for p in open_positions:
            sector_mv[p.sector] = sector_mv.get(p.sector, 0.0) + p.dollar_val

        for _, row in candidates.iterrows():
            ticker = row['ticker']
            if any(p.ticker == ticker for p in open_positions): continue
            
            sector = row['sector'] or 'Miscellaneous'
            rs_val = sector_rs_lookup.get(sector, 1.0)
            base_cap = config.E1_SECTOR_BUDGETS.get(sector, 0.20)
            
            # Apply L2 Dynamic Sector Cap
            effective_cap_pct = e1_sizer.compute_dynamic_sector_cap(
                sector=sector,
                base_cap=base_cap,
                sector_rs=rs_val,
                regime=regime
            )
            
            sector_exposure_pct = sector_mv.get(sector, 0.0) / portfolio_equity
            
            if sector_exposure_pct >= effective_cap_pct:
                continue

            # Compute Position Size (V1.4)
            res = e1_sizer.compute_position_size(
                ticker=ticker,
                ensemble_score=row['ensemble_score'],
                close_price=row['close_price'],
                atr_14=row['atr_14'],
                regime=regime,
                portfolio_value=portfolio_equity,
                cash_available=cash,
                open_positions=[{'ticker': p.ticker, 'dollar_value': p.dollar_val, 'sector': p.sector} for p in open_positions],
                sector=sector,
                vix_close=vix,
                hy_spread=hy,
                sector_cap_pct=effective_cap_pct,
                remaining_sector_budget=(portfolio_equity * effective_cap_pct) - sector_mv.get(sector, 0.0)
            )
            
            if not res['skipped'] and res['shares'] > 0 and cash >= res['dollar_value']:
                new_trade = Trade(
                    ticker=ticker,
                    entry_date=today_date,
                    entry_price=row['close_price'],
                    shares=res['shares'],
                    dollar_val=res['dollar_value'],
                    sector=sector
                )
                
                cash -= res['dollar_value']
                sector_mv[sector] = sector_mv.get(sector, 0.0) + res['dollar_value']
                open_positions.append(new_trade)

        # Mark to market
        current_market_val = sum(p.dollar_val for p in open_positions)
        portfolio_equity = cash + current_market_val
        equity_curve.append({'date': today_date, 'equity': portfolio_equity})

    # Output Results
    print(f"--- TRADE LOG (2026 YTD) ---")
    print(f"{'Ticker':<8} | {'Entry':<12} | {'Exit':<12} | {'Days':<4} | {'PnL%':<8} | {'PnL$':<8}")
    print("-" * 75)
    
    for t in closed_trades:
        print(f"{t.ticker:<8} | {str(t.entry_date):<12} | {str(t.exit_date):<12} | {t.days_held:<4} | {t.pnl_pct*100:>7.2f}% | ${t.pnl_dollars:>7.2f}")

    total_return = (portfolio_equity / INITIAL_CAPITAL) - 1
    win_rate = sum(1 for t in closed_trades if t.pnl_pct > 0) / len(closed_trades) if closed_trades else 0
    
    # CAGR Calculation
    days_range = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days
    cagr = ((1 + total_return) ** (365/days_range) - 1) * 100 if days_range > 0 else 0

    print(f"\n{'='*60}")
    print(f"  FINAL PERFORMANCE SUMMARY")
    print(f"{'='*60}")
    print(f"  Total Trades:   {len(closed_trades)}")
    print(f"  Win Ratio:      {win_rate*100:.1f}%")
    print(f"  Ending Equity:  ${portfolio_equity:,.2f}")
    print(f"  Total Return:   {total_return*100:.2f}%")
    print(f"  Annualized CAGR: {cagr:.2f}%")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    run_v14_relay()
