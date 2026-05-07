"""
Live Piotroski F-Score Calculator — Multi-Source Hybrid
=========================================================
Computes the 9-point Piotroski score using a tiered data strategy:

Tier 1 (Absolute — Yahoo financialData, daily refresh):
  Point 1: ROA > 0
  Point 2: CFO > 0
  Point 4: Accruals (CFO > Net Income)

Tier 2 (Delta — schwab_spot/simfin quarterly filings):
  Point 3: Delta ROA improved
  Point 5: Delta Leverage decreased
  Point 6: Delta Liquidity improved
  Point 7: No share dilution (shares_outstanding)
  Point 8: Delta Gross Margin improved
  Point 9: Delta Asset Turnover improved
"""
import logging
import json
from datetime import date, datetime

import pandas as pd

logger = logging.getLogger('piotroski')


def _safe_float(val, default=None):
    """Safely convert a value to float, returning default if not possible."""
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _extract_yahoo_financials(con, ticker, sim_date=None):
    """Extract Tier 1 data from yahoo.yahoo_raw -> financialData JSON.

    Returns dict with: operatingCashflow, returnOnAssets, freeCashflow,
    grossMargins, currentRatio, totalDebt, totalRevenue, sharesOutstanding.
    Returns None if no raw data found.
    """
    try:
        query = """
            SELECT raw_json FROM yahoo.yahoo_raw
            WHERE ticker = ?
              AND (? IS NULL OR fetched_at <= CAST(? AS DATE) + INTERVAL 7 DAYS)
            ORDER BY fetched_at DESC LIMIT 1
        """
        sim_date_str = sim_date.isoformat() if sim_date else None
        row = con.execute(query, [ticker, sim_date_str, sim_date_str]).fetchone()

        if not row:
            return None

        raw = json.loads(row[0]) if isinstance(row[0], str) else row[0]

        # Navigate: quoteSummary -> result[0] -> financialData
        qs = raw.get('quoteSummary', {})
        results = qs.get('result', [])
        if not results:
            return None

        fd = results[0].get('financialData', {})
        ds = results[0].get('defaultKeyStatistics', {})

        def _val(d, key):
            v = d.get(key)
            if isinstance(v, dict):
                return v.get('raw')
            return v

        return {
            'operatingCashflow': _val(fd, 'operatingCashflow'),
            'freeCashflow': _val(fd, 'freeCashflow'),
            'returnOnAssets': _val(fd, 'returnOnAssets'),
            'returnOnEquity': _val(fd, 'returnOnEquity'),
            'grossMargins': _val(fd, 'grossMargins'),
            'currentRatio': _val(fd, 'currentRatio'),
            'totalDebt': _val(fd, 'totalDebt'),
            'totalRevenue': _val(fd, 'totalRevenue'),
            'totalCash': _val(fd, 'totalCash'),
            'sharesOutstanding': _val(ds, 'sharesOutstanding')
                or _val(fd, 'sharesOutstanding'),
        }
    except Exception as e:
        logger.warning(f"Failed to extract Yahoo financials for {ticker}: {e}")
        return None


def _get_yahoo_shares(con, ticker, sim_date=None):
    """Get shares_outstanding from yahoo.analyst_data as a fallback."""
    try:
        query = """
            SELECT shares_outstanding FROM yahoo.analyst_data
            WHERE ticker = ?
              AND (? IS NULL OR fetched_at <= CAST(? AS DATE) + INTERVAL 7 DAYS)
            ORDER BY fetched_at DESC LIMIT 1
        """
        sim_date_str = sim_date.isoformat() if sim_date else None
        row = con.execute(query, [ticker, sim_date_str, sim_date_str]).fetchone()
        if row and row[0]:
            return float(row[0])
    except Exception:
        pass
    return None


def _get_quarterly_pair(con, ticker):
    """Fetch the two most recent quarterly reports from refined.financials.

    Handles a known issue where consecutive schwab_spot rows contain
    identical derived ratios (TTM snapshots). When detected, falls back
    to comparing the latest schwab_spot row against the most recent
    simfin row for meaningful delta metrics.

    Returns (current_dict, previous_dict) or (None, None).
    """
    try:
        df = con.execute("""
            SELECT report_date, roa, current_ratio, gross_margin,
                   total_debt, total_assets, revenue, total_equity,
                   net_income, operating_cash_flow, source
            FROM refined.financials
            WHERE ticker = ?
            ORDER BY report_date DESC
            LIMIT 5
        """, [ticker]).df()

        if len(df) < 2:
            return None, None

        curr = df.iloc[0].to_dict()
        prev = df.iloc[1].to_dict()

        # Detect schwab_spot duplication: if key ratios are identical,
        # the "previous" row is just a stale copy. Find a genuinely
        # different row (usually the first simfin row).
        if (curr.get('source') == 'schwab_spot'
                and prev.get('source') == 'schwab_spot'
                and curr.get('roa') == prev.get('roa')
                and curr.get('gross_margin') == prev.get('gross_margin')):
            # Search for the first row with different values
            for i in range(2, len(df)):
                candidate = df.iloc[i].to_dict()
                if candidate.get('roa') != curr.get('roa'):
                    prev = candidate
                    break

        return curr, prev
    except Exception as e:
        logger.warning(f"Failed to load quarterly pair for {ticker}: {e}")
        return None, None


def get_precomputed_fscore(con, ticker, sim_date=None):
    """
    Fetch a pre-computed Piotroski score from refined.e1_piotroski_history.
    This is the primary authoritative source for PIT-safe scores.

    Returns dict or None if no score found.
    """
    try:
        query = """
            SELECT f_score_raw, f_score_norm, filing_date, thin_score_flag, status, variant
            FROM refined.e1_piotroski_history
            WHERE ticker = ?
              AND (? IS NULL OR score_date <= CAST(? AS DATE))
            ORDER BY score_date DESC, filing_date DESC
            LIMIT 1
        """
        sim_date_str = sim_date.isoformat() if sim_date else None
        row = con.execute(query, [ticker, sim_date_str, sim_date_str]).fetchone()

        if row:
            # Reconstruct the result dictionary to match compute_piotroski_live return contract
            # Note: Individual points (1-9) and details are not stored in history,
            # so we provide indicators that this was a pre-computed lookup.
            f_score_raw = row[0]
            filing_date = row[2]
            status = row[4]
            
            # Staleness calculation relative to sim_date
            staleness_days = None
            if filing_date:
                reference_date = sim_date if sim_date else date.today()
                # Ensure filing_date is a date object
                if isinstance(filing_date, str):
                    f_dt = datetime.strptime(filing_date, '%Y-%m-%d').date()
                elif hasattr(filing_date, 'date'):
                    f_dt = filing_date.date()
                else:
                    f_dt = filing_date
                staleness_days = (reference_date - f_dt).days

            return {
                'f_score': f_score_raw,
                'points': {}, # Not stored in history
                'detail': {0: f"Pre-computed from EDGAR ({status})"}, 
                'source': f'edgar_{status.lower()}',
                'staleness_days': staleness_days,
                'warnings': [],
            }
    except Exception as e:
        logger.warning(f"Failed to fetch pre-computed F-Score for {ticker}: {e}")
    return None


def compute_piotroski_live(ticker, con, sim_date=None):
    """Calculate 9-point Piotroski F-Score from multi-source data.

    Architecture:
      Tier 0 (authoritative): refined.e1_piotroski_history (pre-computed XBRL)
      Tier 1 (absolute): Yahoo financialData (daily refresh)
      Tier 2 (delta): schwab_spot/simfin quarterly reports

    Returns dict:
      f_score: int (0-9)
      points: dict {1: bool/None, ..., 9: bool/None}
      detail: dict {1: str, ..., 9: str}  -- human-readable explanation
      source: str  -- 'hybrid', 'simfin_only', 'yahoo_tier1_only', 'edgar_full', 'edgar_thin'
      staleness_days: int  -- days since most recent quarterly report
      warnings: list[str]
    """
    # ── TIER 0: Authoritative Pre-computed Score ──────────────────────────
    precomputed = get_precomputed_fscore(con, ticker, sim_date)
    if precomputed:
        return precomputed

    points = {}
    detail = {}
    warnings = []

    # ── Fetch data from all sources ─────────────────────────────────────
    yahoo = _extract_yahoo_financials(con, ticker, sim_date)
    curr, prev = _get_quarterly_pair(con, ticker)

    # Determine source label
    has_yahoo = yahoo is not None
    has_quarterly = curr is not None and prev is not None
    if has_yahoo and has_quarterly:
        source = 'hybrid'
    elif has_yahoo:
        source = 'yahoo_tier1_only'
    elif has_quarterly:
        source = 'simfin_only'
    else:
        return {
            'f_score': None,
            'points': {},
            'detail': {},
            'source': 'no_data',
            'staleness_days': None,
            'warnings': ['No Yahoo or quarterly data available'],
        }

    # Staleness calculation
    staleness_days = None
    if curr:
        rd = curr.get('report_date')
        if rd:
            if isinstance(rd, str):
                rd = datetime.strptime(rd, '%Y-%m-%d').date()
            elif hasattr(rd, 'date'):
                rd = rd.date()
            reference_date = sim_date if sim_date else date.today()
            staleness_days = (reference_date - rd).days
            if staleness_days > 120:
                warnings.append(
                    f"Quarterly data is {staleness_days}d old (>120d threshold)"
                )

    # ── TIER 1: Absolute Metrics (Yahoo) ────────────────────────────────

    # Point 1: ROA > 0
    roa_yahoo = _safe_float(yahoo.get('returnOnAssets')) if yahoo else None
    roa_schwab = _safe_float(curr.get('roa')) if curr else None
    roa_current = roa_yahoo if roa_yahoo is not None else roa_schwab

    if roa_current is not None:
        points[1] = roa_current > 0
        detail[1] = f"ROA = {roa_current:.4f} ({'PASS' if points[1] else 'FAIL'})"
    else:
        points[1] = None
        detail[1] = "ROA unavailable"
        warnings.append("POINT1_ROA_MISSING")

    # Point 2: CFO > 0
    cfo_yahoo = _safe_float(yahoo.get('operatingCashflow')) if yahoo else None
    cfo_schwab = _safe_float(curr.get('operating_cash_flow')) if curr else None
    cfo_current = cfo_yahoo if cfo_yahoo is not None else cfo_schwab

    if cfo_current is not None:
        points[2] = cfo_current > 0
        detail[2] = f"CFO = ${cfo_current/1e9:.1f}B ({'PASS' if points[2] else 'FAIL'})"
    else:
        points[2] = None
        detail[2] = "CFO unavailable"
        warnings.append("POINT2_CFO_MISSING")

    # Point 4: Accruals — CFO > Net Income (earnings quality)
    ni_schwab = _safe_float(curr.get('net_income')) if curr else None
    if cfo_current is not None and ni_schwab is not None:
        points[4] = cfo_current > ni_schwab
        detail[4] = (
            f"CFO ${cfo_current/1e9:.1f}B vs NI ${ni_schwab/1e9:.1f}B "
            f"({'PASS' if points[4] else 'FAIL'})"
        )
    else:
        points[4] = None
        detail[4] = "CFO or NI unavailable for accruals check"
        warnings.append("POINT4_ACCRUALS_MISSING")

    # ── TIER 2: Delta Metrics (Quarterly) ───────────────────────────────

    if has_quarterly:
        # Point 3: Delta ROA > 0 (improving profitability)
        roa_curr_q = _safe_float(curr.get('roa'))
        roa_prev_q = _safe_float(prev.get('roa'))
        if roa_curr_q is not None and roa_prev_q is not None:
            points[3] = roa_curr_q > roa_prev_q
            detail[3] = (
                f"ROA {roa_curr_q:.4f} vs prior {roa_prev_q:.4f} "
                f"({'PASS' if points[3] else 'FAIL'})"
            )
        else:
            points[3] = None
            detail[3] = "ROA delta unavailable"

        # Point 5: Delta Leverage decreased (debt/assets)
        debt_curr = _safe_float(curr.get('total_debt'))
        assets_curr = _safe_float(curr.get('total_assets'))
        debt_prev = _safe_float(prev.get('total_debt'))
        assets_prev = _safe_float(prev.get('total_assets'))

        if all(v is not None and v > 0 for v in [assets_curr, assets_prev]):
            lev_curr = (debt_curr or 0) / assets_curr
            lev_prev = (debt_prev or 0) / assets_prev
            points[5] = lev_curr <= lev_prev
            detail[5] = (
                f"Leverage {lev_curr:.3f} vs prior {lev_prev:.3f} "
                f"({'PASS' if points[5] else 'FAIL'})"
            )
        else:
            points[5] = None
            detail[5] = "Leverage delta unavailable"

        # Point 6: Delta Liquidity (current ratio improved)
        cr_curr = _safe_float(curr.get('current_ratio'))
        cr_prev = _safe_float(prev.get('current_ratio'))
        if cr_curr is not None and cr_prev is not None:
            points[6] = cr_curr > cr_prev
            detail[6] = (
                f"Current Ratio {cr_curr:.3f} vs prior {cr_prev:.3f} "
                f"({'PASS' if points[6] else 'FAIL'})"
            )
        else:
            points[6] = None
            detail[6] = "Current ratio delta unavailable"

        # Point 7: No equity dilution (shares_outstanding)
        shares_curr = _get_yahoo_shares(con, ticker, sim_date)
        # For prior period: use schwab or approximate from equity
        try:
            prior_shares_row = con.execute("""
                SELECT shares_outstanding FROM schwab.equity_fundamentals
                WHERE symbol = ? ORDER BY recorded_at ASC LIMIT 1
            """, [ticker]).fetchone()
            shares_prev = float(prior_shares_row[0]) if prior_shares_row else None
        except Exception:
            shares_prev = None

        if shares_curr is not None and shares_prev is not None:
            # 1% tolerance for rounding
            points[7] = shares_curr <= shares_prev * 1.01
            detail[7] = (
                f"Shares {shares_curr/1e9:.3f}B vs prior {shares_prev/1e9:.3f}B "
                f"({'PASS — no dilution' if points[7] else 'FAIL — dilution'})"
            )
        else:
            points[7] = None
            detail[7] = "Shares outstanding data insufficient"
            warnings.append("POINT7_SHARES_MISSING")

        # Point 8: Delta Gross Margin improved
        gm_curr = _safe_float(curr.get('gross_margin'))
        gm_prev = _safe_float(prev.get('gross_margin'))
        if gm_curr is not None and gm_prev is not None:
            points[8] = gm_curr > gm_prev
            detail[8] = (
                f"Gross Margin {gm_curr:.4f} vs prior {gm_prev:.4f} "
                f"({'PASS' if points[8] else 'FAIL'})"
            )
        else:
            points[8] = None
            detail[8] = "Gross margin delta unavailable"

        # Point 9: Delta Asset Turnover improved (revenue/assets)
        rev_curr = _safe_float(curr.get('revenue'))
        rev_prev = _safe_float(prev.get('revenue'))
        if (all(v is not None and v > 0 for v in [rev_curr, assets_curr])
                and all(v is not None and v > 0 for v in [rev_prev, assets_prev])):
            at_curr = rev_curr / assets_curr
            at_prev = rev_prev / assets_prev
            points[9] = at_curr > at_prev
            detail[9] = (
                f"Turnover {at_curr:.4f} vs prior {at_prev:.4f} "
                f"({'PASS' if points[9] else 'FAIL'})"
            )
        else:
            points[9] = None
            detail[9] = "Asset turnover delta unavailable"
    else:
        # No quarterly data — all delta points are unknown
        for p in [3, 5, 6, 7, 8, 9]:
            points[p] = None
            detail[p] = "No quarterly pair available for delta calculation"
        warnings.append("TIER2_NO_QUARTERLY_DATA")

    # ── Score Aggregation ───────────────────────────────────────────────
    # Fetch sector for exclusions
    try:
        sector_row = con.execute("SELECT sector FROM refined.tickers WHERE ticker = ?", [ticker]).fetchone()
        sector = sector_row[0] if sector_row else None
    except Exception:
        sector = None

    # Define excluded points by sector (e.g., Banks don't have Gross Margin or Current Ratio)
    EXCLUDED_POINTS_BY_SECTOR = {
        'Financial Services': [6, 8]
    }
    excluded_for_this_ticker = EXCLUDED_POINTS_BY_SECTOR.get(sector, [])

    scored_points = [v for p, v in points.items() if v is not None and p not in excluded_for_this_ticker]
    passing = sum(1 for v in scored_points if v)
    total_possible = len(scored_points)

    if total_possible == 0:
        f_score = None
    else:
        # Normalize score out of 9 (e.g. 3/3 becomes 9, 2/3 becomes 6)
        f_score = round((passing / total_possible) * 9)
        
        expected_points = 9 - len(excluded_for_this_ticker)
        if total_possible < expected_points:
            # Fewer than the expected applicable points were evaluated — flag low confidence
            warnings.append(
                f"LOW_CONFIDENCE: Only {total_possible}/{expected_points} points evaluated"
            )

    return {
        'f_score': f_score,
        'points': points,
        'detail': detail,
        'source': source,
        'staleness_days': staleness_days,
        'warnings': warnings,
    }


def write_live_score(con, ticker, result):
    """Write the computed live score back to refined.financials.

    Updates the most recent row for this ticker with the live score fields.
    """
    if result['f_score'] is None:
        return

    try:
        con.execute("""
            UPDATE refined.financials SET
                piotroski_f_score_live = ?,
                piotroski_source = ?,
                piotroski_computed_at = CURRENT_TIMESTAMP,
                piotroski_staleness_days = ?
            WHERE ticker = ?
              AND report_date = (
                  SELECT MAX(report_date) FROM refined.financials
                  WHERE ticker = ?
              )
        """, [
            result['f_score'],
            result['source'],
            result['staleness_days'],
            ticker,
            ticker,
        ])
    except Exception as e:
        logger.warning(f"Failed to write live score for {ticker}: {e}")
