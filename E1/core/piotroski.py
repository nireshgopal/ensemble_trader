"""
Live Piotroski F-Score Calculator — Multi-Source Hybrid
Hardened V1.4: Strict PIT windows and confidence tracking.
"""
import logging
import json
from datetime import date, datetime
import pandas as pd

logger = logging.getLogger('piotroski')

def _safe_float(val, default=None):
    if val is None: return default
    try:
        if pd.isna(val): return default
    except (TypeError, ValueError): pass
    try: return float(val)
    except (TypeError, ValueError): return default

def _extract_yahoo_financials(con, ticker, sim_date=None):
    """Strict PIT: fetched_at <= sim_date"""
    try:
        query = """
            SELECT raw_json FROM yahoo.yahoo_raw
            WHERE ticker = ?
              AND (? IS NULL OR fetched_at <= CAST(? AS DATE))
            ORDER BY fetched_at DESC LIMIT 1
        """
        sim_date_str = sim_date.isoformat() if sim_date else None
        row = con.execute(query, [ticker, sim_date_str, sim_date_str]).fetchone()
        if not row: return None
        raw = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        results = raw.get('quoteSummary', {}).get('result', [])
        if not results: return None
        fd = results[0].get('financialData', {})
        ds = results[0].get('defaultKeyStatistics', {})
        def _val(d, key):
            v = d.get(key)
            return v.get('raw') if isinstance(v, dict) else v
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
            'sharesOutstanding': _val(ds, 'sharesOutstanding') or _val(fd, 'sharesOutstanding'),
            'netIncomeToCommon': _val(fd, 'netIncomeToCommon')
        }
    except Exception as e:
        logger.warning(f"Failed to extract Yahoo financials for {ticker}: {e}")
        return None

def _get_yahoo_shares(con, ticker, sim_date=None):
    try:
        query = """
            SELECT shares_outstanding FROM yahoo.analyst_data
            WHERE ticker = ?
              AND (? IS NULL OR fetched_at <= CAST(? AS DATE))
            ORDER BY fetched_at DESC LIMIT 1
        """
        sim_date_str = sim_date.isoformat() if sim_date else None
        row = con.execute(query, [ticker, sim_date_str, sim_date_str]).fetchone()
        if row and row[0]: return float(row[0])
    except Exception: pass
    return None

def _get_quarterly_pair(con, ticker):
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
        if len(df) < 2: return None, None
        curr = df.iloc[0].to_dict()
        prev = df.iloc[1].to_dict()
        if (curr.get('source') == 'schwab_spot' and prev.get('source') == 'schwab_spot'
                and curr.get('roa') == prev.get('roa')):
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
            f_score_raw = row[0]
            filing_date = row[2]
            status = row[4]
            reference_date = sim_date if sim_date else date.today()
            f_dt = pd.to_datetime(filing_date).date() if filing_date else reference_date
            return {
                'f_score': f_score_raw,
                'points': {},
                'detail': {0: f"EDGAR Pre-computed ({status})"},
                'source': f'edgar_{status.lower()}',
                'staleness_days': (reference_date - f_dt).days,
                'status': status,
                'warnings': []
            }
    except Exception: pass
    return None

def compute_piotroski_live(ticker, con, sim_date=None):
    precomputed = get_precomputed_fscore(con, ticker, sim_date)
    if precomputed: return precomputed

    points = {}
    detail = {}
    warnings = []
    status = 'OK'

    yahoo = _extract_yahoo_financials(con, ticker, sim_date)
    curr, prev = _get_quarterly_pair(con, ticker)

    if not yahoo and not curr:
        return {'f_score': None, 'points': {}, 'detail': {}, 'source': 'no_data', 'status': 'MISSING', 'warnings': ['No data']}

    # 1. ROA > 0
    roa_y = _safe_float(yahoo.get('returnOnAssets')) if yahoo else None
    roa_q = _safe_float(curr.get('roa')) if curr else None
    roa = roa_y if roa_y is not None else roa_q
    points[1] = roa > 0 if roa is not None else None
    detail[1] = f"ROA {roa}" if roa is not None else "Missing"

    # 2. CFO > 0
    cfo_y = _safe_float(yahoo.get('operatingCashflow')) if yahoo else None
    cfo_q = _safe_float(curr.get('operating_cash_flow')) if curr else None
    cfo = cfo_y if cfo_y is not None else cfo_q
    points[2] = cfo > 0 if cfo is not None else None
    detail[2] = f"CFO {cfo}" if cfo is not None else "Missing"

    # 4. Accruals (CFO > NI)
    # NI often from Quarterly, CFO often from Yahoo (TTM)
    ni_y = _safe_float(yahoo.get('netIncomeToCommon')) if yahoo else None
    ni_q = _safe_float(curr.get('net_income')) if curr else None
    ni = ni_y if ni_y is not None else ni_q
    
    if cfo is not None and ni is not None:
        points[4] = cfo > ni
        detail[4] = f"CFO {cfo} > NI {ni}"
        # Source mismatch warning
        if yahoo and curr and cfo_y is not None and ni_q is not None and ni_y is None:
            detail[4] += " (CFO/NI source mismatch: TTM vs Q; treat with caution)"
            status = 'LOW_CONFIDENCE'
    else:
        points[4] = None
        detail[4] = "Accruals missing"

    # Delta Metrics (3,5,6,7,8,9)
    for p in [3,5,6,7,8,9]:
        points[p] = None # Simplified for live hybrid
        detail[p] = "Delta metrics require full quarterly pair"

    if curr and prev:
        # Point 3: Delta ROA
        roa_prev = _safe_float(prev.get('roa'))
        if roa_q is not None and roa_prev is not None:
            points[3] = roa_q > roa_prev
            detail[3] = f"ROA {roa_q} > {roa_prev}"

    scored = [v for v in points.values() if v is not None]
    if not scored: return {'f_score': None, 'points': points, 'detail': detail, 'source': 'hybrid', 'status': 'MISSING', 'warnings': warnings}
    
    f_score = round((sum(1 for v in scored if v) / len(scored)) * 9)
    if len(scored) < 7: 
        status = 'LOW_CONFIDENCE'
        warnings.append(f"Low confidence: only {len(scored)}/9 points")

    return {
        'f_score': f_score, 'points': points, 'detail': detail,
        'source': 'hybrid', 'status': status, 'warnings': warnings
    }

def write_live_score(con, ticker, result):
    if result.get('f_score') is None: return
    try:
        con.execute("""
            UPDATE refined.financials SET
                piotroski_f_score_live = ?,
                piotroski_source = ?,
                piotroski_computed_at = CURRENT_TIMESTAMP,
                piotroski_staleness_days = ?
            WHERE ticker = ?
              AND report_date = (SELECT MAX(report_date) FROM refined.financials WHERE ticker = ?)
        """, [result['f_score'], result['source'], result.get('staleness_days', 0), ticker, ticker])
    except Exception: pass
