# E1 V1.4 — Sprint 2 & Sprint 3 Implementation Prompts for Flash (Antigravity)
**Date:** May 3, 2026  
**Strategy:** E1 V1.4  
**Scope:** Sprint 2 (Data Integrity & Shadow Validation) + Sprint 3 (Statistical Rigor & Governance)  
**Pre-requisite:** Sprint 1 prompts must be completed and validated first.  
**How to use:** Paste each prompt individually into Flash on Antigravity. Each is self-contained.

---

## SPRINT 2 — Data Integrity & Shadow Validation
*These fixes make the backtest numbers trustworthy and prevent SQL/Monitor breakage.*  
*Complete these before the 60-session gate evaluation.*

---

---

## PROMPT S2-1 of 8 — F-03: Piotroski Yahoo PIT Look-Ahead Bias
**Finding:** F-03 | Severity: 🔴 CRITICAL | File: `piotroski.py`  
**Effort estimate:** 2 hours + shadow rerun time

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix a total look-ahead bias in `piotroski.py` that invalidates all shadow run 
backtest results. 100% of the Yahoo fundamental data used in the 2018-2022 
shadow runs was fetched in 2026 (confirmed: earliest fetched_at in yahoo.yahoo_raw 
is 2026-03-15). This means the OOS CAGR of 19.6% and 2020 stress test result of 
+68.3% must be treated as preliminary until a clean Point-in-Time (PIT) rerun completes.

## The Bug — Two Affected Functions

### Bug 1: _extract_yahoo_financials() / extract_yahoo_financials()
Currently queries:
    SELECT raw_json FROM yahoo.yahoo_raw
    WHERE ticker = ?
    ORDER BY fetched_at DESC LIMIT 1

This always returns the MOST RECENT data (from 2026), regardless of the 
simulation date. In a shadow run for 2018-01-15, it returns 2026 balance sheet data.

### Bug 2: _get_yahoo_shares() / get_yahoo_shares()
Currently queries:
    SELECT shares_outstanding FROM yahoo.analyst_data
    WHERE ticker = ?
    ORDER BY fetched_at DESC LIMIT 1

Same problem — always returns the most recent share count, not the share count 
as of the simulation date. This affects Point 7 (dilution check).

## The Fix

### Fix 1: Add sim_date parameter to extract_yahoo_financials()

Change the signature from:
    def extract_yahoo_financials(con, ticker):
To:
    def extract_yahoo_financials(con, ticker, sim_date=None):

Change the query to:
    SELECT raw_json FROM yahoo.yahoo_raw
    WHERE ticker = ?
      AND (? IS NULL OR fetched_at <= CAST(? AS DATE) + INTERVAL 7 DAYS)
    ORDER BY fetched_at DESC LIMIT 1

(The +7 day buffer accounts for the typical Yahoo data publishing lag — 
fundamentalData is usually published within a week of the filing date.)

If no sim_date is provided (production mode), the query behaves identically 
to before (returns latest). In shadow mode, it returns the most recent data 
available as of sim_date + 7 days.

### Fix 2: Add sim_date parameter to get_yahoo_shares()

Change the signature from:
    def get_yahoo_shares(con, ticker):
To:
    def get_yahoo_shares(con, ticker, sim_date=None):

Change the query to:
    SELECT shares_outstanding FROM yahoo.analyst_data
    WHERE ticker = ?
      AND (? IS NULL OR fetched_at <= CAST(? AS DATE) + INTERVAL 7 DAYS)
    ORDER BY fetched_at DESC LIMIT 1

### Fix 3: Propagate sim_date through compute_piotroski_live()

Change:
    def compute_piotroski_live(ticker, con):
To:
    def compute_piotroski_live(ticker, con, sim_date=None):

Pass sim_date to both extract_yahoo_financials() and get_yahoo_shares() calls 
inside compute_piotroski_live().

### Fix 4: Update the call site in e1_trader.py

Find where compute_piotroski_live() is called in the entry veto check block 
(search for "piotroski" or "f_score"). Add sim_date=effectivedate to the call:
    result = piotroski.compute_piotroski_live(ticker, conn, sim_date=effectivedate)

### Fix 5: Add to shadow_runner monkey-patch list (if applicable)

In the shadow runner / test harness, wherever _get_quarterly_pair is 
monkey-patched to inject sim_date, add the same pattern for 
_extract_yahoo_financials and _get_yahoo_shares. They should all receive 
the same sim_date.

## Acceptance Criteria
- extract_yahoo_financials() and get_yahoo_shares() both accept an optional sim_date
- When sim_date is provided, only records with fetched_at <= sim_date + 7 days are returned
- When sim_date is None (production), behavior is unchanged
- compute_piotroski_live() passes sim_date through to both sub-functions
- The call site in e1_trader.py passes effectivedate as sim_date
- A unit test verifies: given a ticker with two yahoo_raw rows (one from 2019, 
  one from 2026), calling with sim_date=2019-06-01 returns the 2019 row
- The staleness_days calculation at the bottom of compute_piotroski_live() 
  currently uses date.today() — change it to use sim_date if provided:
    reference_date = sim_date if sim_date else date.today()
    staleness_days = (reference_date - report_date).days
```

---

---

## PROMPT S2-2 of 8 — F-09: Implement Consecutive-Window Rule in Monitor
**Finding:** F-09 | Severity: 🔴 CRITICAL | File: `e1_monitor.py`  
**Effort estimate:** 30 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Implement the "two consecutive windows" escalation rule in e1_monitor.py. 
The current monitor fires a WARNING on a SINGLE 30-session breach. 
The E1 Specification (§6.1) explicitly states:
  "T2 Hit Rate Warning: < 10% for TWO consecutive 30-session windows → 
   trigger full weight and threshold audit."

DB evidence confirms: e1_performance_audit already has consecutive WARNING 
rows logged for May 2nd and 3rd with zero system escalation — the rule 
was never implemented.

## The Bug
In the threshold evaluation block (search for "status = 'WARNING'"), the 
monitor sets status to WARNING on a single breach and immediately writes 
it. There is no check of the prior row before deciding whether to escalate.

## The Fix

After computing the current metrics but BEFORE writing to the audit table, 
add a query to check the previous audit row:

    prev_row = con.execute(f"""
        SELECT status FROM {AUDIT_TABLE}
        WHERE audit_date < ?
        ORDER BY audit_date DESC LIMIT 1
    """, [today_str]).fetchone()
    prev_status = prev_row[0] if prev_row else 'HEALTHY'

Then add consecutive-window escalation logic:

1. If current status == 'WARNING' AND prev_status == 'WARNING':
   - Upgrade status to 'CRITICAL'
   - Append to flags: "CONSECUTIVE WARNING — Full audit required"
   - Send a separate, elevated Telegram alert with header:
     "🚨 E1 CONSECUTIVE WARNING — AUDIT REQUIRED"
     Body: "T2 Hit Rate has been below 10% for two consecutive 30-session 
     windows. Per Spec §6.1: Full weight and threshold audit is mandatory 
     before next session."

2. If current status == 'WARNING' AND prev_status != 'WARNING':
   - Keep status as 'WARNING' (first breach, no escalation yet)
   - Standard Telegram warning message (already exists)

3. If current status == 'HEALTHY':
   - No change needed

## Acceptance Criteria
- A query checks the most recent prior audit row before status is finalized
- Two consecutive WARNING rows trigger status='CRITICAL' and an elevated alert
- A single WARNING row does not escalate
- The elevated Telegram message is distinct from the standard WARNING message
- The audit table INSERT writes the escalated 'CRITICAL' status (not 'WARNING') 
  when consecutive breaches are detected
- The same consecutive-check logic applies to the BE Stop Ratio metric as well 
  (if BE ratio has been 'CRITICAL' for two consecutive sessions, re-alert)
- Simulate mode: log what the escalation decision would be without sending 
  Telegram or writing to DB
```

---

---

## PROMPT S2-3 of 8 — F-05: Fix Monitor String Matching (T2 + Stop Case-Sensitivity)
**Finding:** F-05 | Severity: 🟠 HIGH | File: `e1_monitor.py`  
**Effort estimate:** 30 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix two string-matching bugs in e1_monitor.py that cause metrics to be silently 
miscalculated, making the monitor report false-healthy numbers.

## Bug 1: T2 Detection — Fragile Substring Match
Current code:
    t2_hits = len(df[df['exit_trigger'].str.contains('Target 2', na=False)])

This will FAIL to match if the canonical trigger string ever changes casing 
or format (e.g., 'TARGET_2_HIT', 'Target_2', 'T2_HIT'). DB evidence confirms 
triggers like 'Target 2 ($142.50) reached intraday.' are the current format, 
but this is fragile.

Fix: Replace with an exact-match check using the canonical constant:
    t2_hits = len(df[df['exit_trigger'].str.startswith('Target 2', na=False)])

Better: query the config module for the canonical trigger string and use:
    T2_TRIGGER_PREFIX = 'Target 2'
    t2_hits = len(df[df['exit_trigger'].str.startswith(T2_TRIGGER_PREFIX, na=False)])

## Bug 2: Stop Detection — Wrong Case, Misses Emergency Liquidations
Current code:
    stops = df[df['exit_trigger'].str.contains('Stop', na=False)]

DB evidence confirms actual trigger strings in production are:
  - 'STOP_VIOLATION'
  - 'Stop 150.20 breached.'
  - 'HEAL_ABORT_NO_POSITION'

The capital 'S' in 'Stop' does NOT match 'STOP_VIOLATION' because 
str.contains is case-sensitive by default. Emergency liquidations 
(STOP_VIOLATION) — the most capital-destructive exits — are systematically 
excluded from the Breakeven Ratio metric.

Fix: Use case-insensitive matching:
    stops = df[df['exit_trigger'].str.contains('stop', case=False, na=False)]

This catches 'STOP_VIOLATION', 'Stop 150.20 breached.', and any future 
variants regardless of casing.

## Bug 3 (While You Are Here): Time Exit PnL Flag Does Not Promote Status
Current code sets a flag for time exit drift but never promotes status to WARNING:
    if time_exit_avg < -50.0:
        flags.append(f"Time Exit Drift {time_exit_avg:.2f} < -$50 Decay")
    # status remains HEALTHY ← BUG

Per Spec §6.1, two consecutive windows with HEALTHY Time Exit avg below -$50 
should shorten the time-exit horizon. The monitor should at least flag it 
visibly. Fix:
    if time_exit_avg < -50.0:
        if status == 'HEALTHY':
            status = 'WARNING'   # promote
        flags.append(f"Time Exit Drift {time_exit_avg:.2f} < -$50 Decay")

## Acceptance Criteria
- T2 detection uses str.startswith('Target 2') or equivalent prefix match
- Stop detection uses str.contains('stop', case=False) — captures STOP_VIOLATION
- Time exit drift below -$50 promotes status from HEALTHY to WARNING
- All three fixes are in the same commit — they are all in e1_monitor.py
- Add a comment above each fixed line explaining what was wrong and why
- A log message at INFO level prints the raw counts: 
  f"Monitor raw counts: t2_hits={t2_hits}, stops={len(stops)}, time_exits={len(time_exits)}"
  This makes future debugging trivial.
```

---

---

## PROMPT S2-4 of 8 — F-17: Inject sim_date and conn into Monitor
**Finding:** F-17 | Severity: 🟠 HIGH | File: `e1_monitor.py`  
**Effort estimate:** 30 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix e1_monitor.py so it respects the simulation date in shadow mode. Currently 
the monitor hardcodes date.today() and opens its own DuckDB connection, meaning 
in shadow mode it queries production tables with a 2026 date regardless of the 
simulation timeline. All shadow-mode audit data is corrupt.

## The Bug
In run_performance_monitor():
1. The audit date is hardcoded: today_str = date.today().isoformat()
2. The connection is always a new one: con = duckdb.connect(DB_PATH)
3. The closed trades query has no date filter tied to sim_date

## The Fix

### Change 1: Add sim_date and conn parameters
Change the function signature from:
    def run_performance_monitor(simulate=False):
To:
    def run_performance_monitor(simulate=False, sim_date=None, conn=None):

### Change 2: Use sim_date for audit date
Replace:
    today_str = date.today().isoformat()
With:
    reference_date = sim_date if sim_date else date.today()
    today_str = reference_date.isoformat()

### Change 3: Use injected connection or open a new one
Replace:
    con = duckdb.connect(DB_PATH)
With:
    _owns_conn = conn is None
    con = conn if conn is not None else duckdb.connect(DB_PATH)

And in the finally block, only close if we opened it:
    finally:
        if _owns_conn:
            con.close()

### Change 4: Scope the closed trades query to sim_date
The query currently fetches the last 30 HEALTHY closed trades globally. 
In shadow mode, it must only look at trades closed ON OR BEFORE sim_date:

Add to the WHERE clause:
    AND (? IS NULL OR exit_date <= ?)

And pass [sim_date, sim_date] as additional parameters to con.execute().

If sim_date is None (production mode), the IS NULL check passes through 
and behavior is unchanged.

### Change 5: Update all callers

In e1_reconciler.py, find where run_performance_monitor() is called 
(search for "e1monitor.run_performance_monitor") and update to:
    e1monitor.run_performance_monitor(
        simulate=simulate, 
        sim_date=today_dt,    # today_dt is already the sim-aware date in reconciler
        conn=conn             # pass the existing connection
    )

## Acceptance Criteria
- run_performance_monitor() accepts sim_date and conn as optional parameters
- In shadow mode (sim_date provided), all date references use sim_date not date.today()
- In production mode (sim_date=None), behavior is completely unchanged
- The connection is only closed if it was opened by this function (_owns_conn=True)
- The closed trades query is scoped to trades on or before sim_date
- The reconciler passes today_dt and conn when calling the monitor
- Simulate mode still skips DB writes and Telegram calls
```

---

---

## PROMPT S2-5 of 8 — F-20: Remove Duplicate Sector Derivation in Entry Loop
**Finding:** F-20 | Severity: 🟠 HIGH | File: `e1_trader.py`  
**Effort estimate:** 15 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix a sector derivation race condition in e1_trader.py where the entry loop 
computes current_mapped_sector twice, with the second computation overwriting 
the first and stripping away MANUAL_SECTOR_OVERRIDES.

## The Bug
In the entry candidate evaluation loop, the sector is derived in two places:

Derivation 1 (correct — with overrides):
    current_mapped_sector = row.get('sector', 'Other')
    if ticker in config.MANUAL_SECTOR_OVERRIDES:
        current_mapped_sector = config.MANUAL_SECTOR_OVERRIDES[ticker]

Derivation 2 (wrong — overwrites the override):
    current_mapped_sector = row.get('sector', 'Other')   ← appears later, no override

The second derivation appears later in the same loop body (often near the sector 
budget check or the last-mile veto), and it overwrites Derivation 1. The result 
is that sector budget accounting uses the data provider's raw sector mapping 
instead of the manually overridden one. Tickers in MANUAL_SECTOR_OVERRIDES get 
their budget charged to the wrong sector bucket.

## The Fix

1. Find both sector derivation points in the entry loop.

2. DELETE the second derivation entirely (the one without the MANUAL_SECTOR_OVERRIDES check).

3. Confirm that current_mapped_sector flows correctly from Derivation 1 through 
   to all downstream uses: sector budget check, sector_counts update, 
   sector_mv update, and the DB INSERT (sector_rs_at_entry, effective_sector_cap).

4. Add a single comment above Derivation 1:
   # Canonical sector: use manual override if configured, else provider mapping.
   # Do NOT re-derive current_mapped_sector below this point in the entry loop.

## Acceptance Criteria
- current_mapped_sector is derived exactly once per entry candidate in the loop
- The derivation includes the MANUAL_SECTOR_OVERRIDES check
- All sector budget accounting downstream uses this single derivation
- Removing the duplicate does not introduce any NameError downstream
- Add a logger.debug line after the derivation:
    logger.debug(f"{ticker} sector: {current_mapped_sector} (override={ticker in config.MANUAL_SECTOR_OVERRIDES})")
- No other logic in the entry loop is changed
```

---

---

## PROMPT S2-6 of 8 — F-16: Migrate Exit DB Queries to Parameterized SQL
**Finding:** F-16 | Severity: 🔴 CRITICAL | File: `e1_trader.py`  
**Effort estimate:** 3 hours

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Migrate the DuckDB INSERT/UPDATE statements in e1_trader.py's exit and entry 
blocks from f-string SQL interpolation to parameterized queries using the ? 
placeholder syntax. This prevents SQL syntax breaks from special characters 
in exit_trigger strings.

## The Bug
Multiple SQL statements in e1_trader.py use f-string interpolation directly 
in the query string:

    conn.execute(f"""
        UPDATE {config.E1_POSITIONS_TABLE}
        SET exit_trigger = '{eval_result.get("reason", "UNKNOWN")}'
        WHERE id = {posid}
    """)

The exit_trigger field regularly contains characters like $, (, ), and ' 
from strings such as "Target 2 ($142.50) reached intraday." that break SQL 
syntax or cause injection. The reactive escape `replace("'", "''")` on L786 
confirms this has already caused issues in production.

## The Fix — Systematic Parameterization

For EVERY conn.execute() in the exit loop, entry block, and stop-promotion 
branches that currently uses f-string variable interpolation for VALUES or SET 
clauses, convert to parameterized form:

### Pattern: Before
    conn.execute(f"""
        UPDATE {config.E1_POSITIONS_TABLE}
        SET status = 'CLOSED',
            exit_trigger = '{reason}',
            pnl_pct = {pnl_pct}
        WHERE id = {posid}
    """)

### Pattern: After
    conn.execute(f"""
        UPDATE {config.E1_POSITIONS_TABLE}
        SET status = 'CLOSED',
            exit_trigger = ?,
            pnl_pct = ?
        WHERE id = ?
    """, [reason, pnl_pct, pid])

### Rules
1. Table names and schema names (e.g., config.E1_POSITIONS_TABLE) MUST remain 
   as f-string interpolation — DuckDB does not allow parameterized table names.
   Only VALUES and WHERE clause variable data should use ? placeholders.

2. The ? parameter list must match the column order exactly. Check every 
   converted statement carefully.

3. Remove all .replace("'", "''") escape workarounds — they are no longer needed 
   once parameterized.

4. Do NOT convert READ queries (SELECT statements) that only use string literals 
   or config constants in their WHERE clauses — those are low risk and out of scope.

## Priority Order (tackle in this order)
1. All UPDATE statements in the exit loop (status=CLOSED, exit_trigger, pnl_pct, 
   pnl_dollars, days_held, exit_price, exit_regime)
2. All INSERT INTO e1_trade_log statements
3. All INSERT INTO e1_positions statements (entry block)
4. All INSERT INTO e1_fills statements
5. All INSERT INTO sandbox.e1_decay_exit_tracking statements

## Acceptance Criteria
- No f-string variable interpolation in VALUES or WHERE clauses of any 
  INSERT/UPDATE statement
- Table names and schema references remain as f-strings (this is correct)
- All .replace("'", "''") escape workarounds are removed
- DuckDB parameterized queries use positional ? placeholders (not named :param)
- The parameter list is passed as the second argument: conn.execute(sql, [p1, p2, ...])
- None values in parameter lists are handled correctly — DuckDB accepts Python 
  None as SQL NULL in parameterized queries
- After the change, run one shadow day and confirm no SQL syntax errors in logs
```

---

---

## PROMPT S2-7 of 8 — F-18 + F-19: Fix Shadow Mode Fidelity (high price alias + MTM)
**Finding:** F-18 + F-19 | Severity: 🟡 MEDIUM | File: `e1_trader.py` (mock client)  
**Effort estimate:** 2 hours

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix two shadow mode fidelity bugs that cause the simulation to systematically 
undercount T2 hits and report stale portfolio valuations.

---

## Bug 1 — F-18: `high` Aliased to `close_price` in Market Data SQL

In the signals query (search for "close_price as high" or the SELECT block 
that builds market_data_lookup), there is an alias:

    s.close_price as high

This means the T2 target check in shadow mode compares against the CLOSE price 
rather than the intraday HIGH price. In real trading, the Alpaca OCO bracket 
fires when the intraday high touches Target 2. But in the shadow run, a position 
only registers a T2 hit if the CLOSE is above Target 2 — which happens far less 
frequently than the intraday high crossing Target 2.

### Fix for Bug 1
In the signals SQL query, change:
    s.close_price as high
To:
    s.high_price as high      ← use the actual daily high column

Verify that `high_price` is a column in `refined.daily_signals_ml`. If the 
column is named differently (e.g., `day_high`, `price_high`), use the correct 
column name by checking the table schema.

If the table does not store intraday highs, add a JOIN to 
refined.price_history to get the high:
    LEFT JOIN refined.price_history ph 
        ON s.ticker = ph.ticker AND s.date = ph.date

And in the SELECT: COALESCE(ph.high, s.close_price) as high

---

## Bug 2 — F-19: Mock Client `dollar_value` Never Marks-to-Market

In the MockAlpacaClient (or mock client class used in shadow mode), the 
`_get_invested_value()` method (or equivalent portfolio value method) sums 
entry-time `dollar_value` from the positions table:

    SELECT SUM(dollar_value) FROM {config.E1_POSITIONS_TABLE} WHERE status = 'OPEN'

`dollar_value` is set at entry time and never updated. Over multi-day shadow 
runs, portfolio value, position sizing, and sector budget calculations all use 
stale equity. A position entered at $5,000 that is now worth $7,500 still 
shows as $5,000 in the budget.

### Fix for Bug 2
Update `_get_invested_value()` to compute current market value using the 
most recent close price from price_history:

    SELECT SUM(p.shares * COALESCE(ph.close, p.entry_price)) as current_mv
    FROM {config.E1_POSITIONS_TABLE} p
    LEFT JOIN (
        SELECT ticker, close
        FROM refined.price_history
        WHERE date = (SELECT MAX(date) FROM refined.price_history WHERE date <= ?)
    ) ph ON p.ticker = ph.ticker
    WHERE p.status = 'OPEN'

Pass `sim_date` (available as `self.sim_date` on the mock client) as the 
date parameter. Fall back to `entry_price` if no price_history row exists.

Also update the mock `get_account()` method to return a portfolio_value that 
uses this MTM calculation rather than a static initialization value.

## Acceptance Criteria
- The signals query uses the actual intraday high column, not close_price
- Shadow T2 detection now fires when intraday high >= target_2, matching 
  real Alpaca OCO behavior
- _get_invested_value() returns mark-to-market value using most recent 
  price_history close, not entry-time dollar_value
- The MTM calculation uses sim_date as the price reference date
- Both fixes are tested by running a single shadow day and confirming:
  (a) at least one T2 detection occurs when a ticker's high exceeds target_2
  (b) portfolio_value changes between days as positions gain/lose value
```

---

---

## PROMPT S2-8 of 8 — F-12 Bundle: Miscellaneous Data Integrity Fixes
**Finding:** F-12 (Finding 3, 5, 9, 10, 12) | Severity: 🟡 MEDIUM | Multiple files  
**Effort estimate:** 1.5 hours

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Implement 5 small but valid data integrity fixes identified in the dual-reviewer 
audit. These are bundled together because each is a small, targeted change.

---

## Fix A — cash_available Drift (e1_trader.py)
Finding 3: cash_available tracks intended deployment, not actual deployment.

In the entry loop, cash_available is decremented by dollar_val when an entry 
is decided, but this happens before the order is confirmed filled. If an order 
fails silently, cash_available shows less cash than is actually available. 
Within a single scan, subsequent candidates may be skipped incorrectly.

Fix: Only decrement cash_available AFTER a successful order submission:
    # Move this line:
    cash_available -= dollar_val
    # To AFTER the try/except order submission block, inside the success path

---

## Fix B — Emergency Liquidation Detection (e1_trader.py)
Finding 5: Emergency liquidation detection uses API message string matching.

The code currently detects stop violations by matching Alpaca error messages 
like "stop price must be less than current price" (string match). This is 
fragile — API error messages can change.

Fix: After catching the exception, attempt to re-fetch the current quote 
and verify mathematically:
    try:
        quote = client.get_stock_latest_quote(...)
        current = float(quote[ticker].ask_price)
        if current < pos.get('stop_loss', float('inf')):
            # Confirmed price violation — proceed with emergency exit
        else:
            # API error but no price violation — log and skip
    except:
        # If re-fetch also fails, use the string match as last resort

Also check for Alpaca error code 42210000 (insufficient buying power / 
validation error) before the string match as the primary detection method.

---

## Fix C — Time Exit PnL Flag in Telegram Summary (e1_monitor.py)
Finding 9: Time Exit PnL flag does not promote status to WARNING.

Already partially covered in Prompt S2-3 (Bug 3), but verify here that 
the Telegram message body also includes a distinct visual marker when 
time_exit_avg is below threshold:

In the msg assembly block, ensure time exit drift appears prominently:
    if time_exit_avg < -50.0:
        msg += f"
⚠️ *Time Exit Drift*: {time_exit_avg:.2f} (below -$50 floor)"

---

## Fix D — Rolling Window Calendar Anchor (e1_monitor.py)
Finding 10: Rolling window has no calendar anchor.

The monitor queries "last 30 HEALTHY closed trades" with no time bound. 
After many months of trading, this could pull trades from 18+ months ago, 
making the window stale as a current performance gauge.

Fix: Add a 180-calendar-day constraint to the query:
    AND exit_date >= CURRENT_DATE - INTERVAL '180 days'

The query already has LIMIT 30 — the 180-day constraint ensures the 30 
trades are recent. If fewer than 10 trades exist within 180 days, 
the existing "insufficient history" guard fires correctly.

---

## Fix E — Piotroski staleness_days uses date.today() in shadow mode (piotroski.py)
Finding 12: staleness_days uses date.today() instead of sim_date.

This is the companion fix to Prompt S2-1. In compute_piotroski_live(), 
the staleness calculation is:
    staleness_days = (date.today() - report_date).days

This always uses the wall clock, so in a 2018 shadow run, a 2018 report 
shows as ~2900 days stale (triggering false staleness warnings) instead 
of 0 days stale relative to the simulation date.

Fix (already mentioned as Acceptance Criteria in S2-1, verify here):
    reference_date = sim_date if sim_date else date.today()
    staleness_days = (reference_date - report_date).days

## Acceptance Criteria
- Fix A: cash_available decremented only after confirmed order submission
- Fix B: Emergency liquidation attempts quote re-fetch before string matching; 
  checks error code 42210000 as primary signal
- Fix C: Time exit drift appears prominently in Telegram notification body
- Fix D: Rolling window query includes AND exit_date >= CURRENT_DATE - INTERVAL '180 days'
- Fix E: staleness_days uses sim_date if provided, else date.today()
- All 5 fixes are in a single commit with clear commit message referencing 
  each finding number
```

---

---

## SPRINT 3 — Statistical Rigor & Governance (Phase 3 Prep)
*These fixes upgrade the quantitative foundations and edge-case handling.*  
*Implement while the Sprint 2 shadow rerun is completing.*

---

---

## PROMPT S3-1 of 4 — F-06: Increase Monitor Window to 60 Sessions
**Finding:** F-06 | Severity: 🟠 HIGH | File: `e1_monitor.py`  
**Effort estimate:** 1 hour

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Increase the statistical power of the rolling monitor windows in e1_monitor.py 
based on rigorous power analysis performed during the dual-reviewer audit.

## The Statistical Problem
Current window: n=30 sessions.

Two specific calculations confirm n=30 is insufficient:

1. T2 Hit Rate (Wilson 95% CI): At n=30, the Wilson confidence interval 
   for a 10% observed rate is [2.1%, 26.5%] — completely overlapping with 
   the 15% target. The WARNING threshold (10%) and the HEALTHY target (15%) 
   are statistically indistinguishable at n=30.

2. Decay Veto Trigger: Under H₀ (true win rate = 50%), P(X ≥ 18 | n=30) ≈ 18% 
   false positive rate. Nearly 1-in-5 triggers would be noise.

## The Fix

### Change 1: Increase T2 monitor window to 60 sessions
In e1_monitor.py, change the LIMIT in the closed trades query from:
    ORDER BY exit_date DESC LIMIT 30
To:
    ORDER BY exit_date DESC LIMIT 60

Update the minimum history guard from:
    if len(df) < 10:
To:
    if len(df) < 20:

### Change 2: Update the insufficient history log message
    logger.info(f"Insufficient trade history ({len(df)}<20) for rolling audit. Skipping.")

### Change 3: Add a "window size" field to the audit table log
Add window_size to the INSERT so the audit trail shows what window was used:
    - Add column `window_size INTEGER` to the AUDIT_TABLE if not exists
    - Pass len(df) as the value (actual trades evaluated, not just the limit)

### Change 4: Confirm the decay veto n ≥ 45 requirement
In e1_reconciler.py or wherever the score decay trigger is evaluated 
(the VETO_COST_ALPHA rate trigger), find the minimum sample size check:
    if total < [some_number]:
        skip or flag low confidence

Change the minimum to 45 if it is currently lower. Add a comment:
    # Requires n>=45 to keep false positive rate below 5% (binomial, p=0.50)
    # Power analysis: P(X>=18|n=30,p=0.50) = 18%; P(X>=23|n=45,p=0.50) = 5%

## Acceptance Criteria
- T2 monitor evaluates last 60 HEALTHY closed trades, not 30
- Minimum history guard requires 20 trades, not 10
- Decay verdict trigger requires n>=45 exits in the tracking table
- window_size is stored in the audit table for each audit run
- All config constants (LIMIT value, minimum guard) are defined as named 
  constants at the top of the file, not hardcoded inline:
    MONITOR_WINDOW = 60
    MONITOR_MIN_TRADES = 20
    DECAY_MIN_SAMPLE = 45
```

---

---

## PROMPT S3-2 of 4 — F-04: Regime Transition Decay Score Baseline Reset
**Finding:** F-04 | Severity: 🟠 HIGH | File: `exit_evaluator.py` (or signal_votes.py)  
**Effort estimate:** 2 hours

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix regime-transition-induced false decay veto triggers in the exit evaluator. 
The current decay check compares today's ensemble score against entry score 
using fixed regime weights, causing phantom "decay" signals on transition days.

## The Problem (Worked Example from Audit)
A position entered in BEAR regime with entry score = 0.38.
On a BEAR → HEALTHY transition day, the same signals now produce score = 0.22 
(because BEAR weight-vector weights RSI Bounce and DD Recovery, while HEALTHY 
weight-vector weights RS and MA Slope — and those signals are near zero).

Ratio: 0.22 / 0.38 = 57.9% — above the 40% decay threshold, so no veto fires.
BUT: if the ratio were 55% or less, the system would veto. The "decay" is entirely 
an artifact of the weight-vector change, not genuine thesis deterioration.

DB evidence: 8 regime jump events identified in the shadow run history.

## The Fix — Regime-Normalized Baseline Reset

### Step 1: Detect regime transition
In the exit evaluation function, after loading current_regime and yesterday_regime, 
check for a transition:
    regime_transitioned = (
        yesterday_regime is not None and 
        current_regime != yesterday_regime
    )

### Step 2: On transition day, reset the decay baseline
If regime_transitioned is True AND the decay veto would otherwise fire:
  a. Do NOT trigger the decay veto on transition day itself
  b. Update the position's `entry_score` in the DB to the current score 
     (the new regime's score becomes the new baseline):

    if regime_transitioned and decay_veto_would_fire:
        new_baseline = current_score
        conn.execute(f"""
            UPDATE {config.E1_POSITIONS_TABLE}
            SET ensemble_score = ?,
                score_at_entry_baseline = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [new_baseline, new_baseline, pid])
        logger.info(
            f"{ticker} REGIME TRANSITION {yesterday_regime}→{current_regime}: "
            f"Decay baseline reset to {new_baseline:.3f} (was {entry_score:.3f}). "
            f"Veto suppressed for this transition day."
        )
        # Return a non-SELL action to continue holding
        return {'action': 'HOLD', 'reason': f'REGIME_TRANSITION_BASELINE_RESET_{current_regime}'}

### Step 3: Add score_at_entry_baseline column if needed
If e1_positions table does not have a `score_at_entry_baseline` column, 
add it via ALTER TABLE (same pattern as existing PHASE 2 schema updates 
at the top of run_e1_trader):
    if 'score_at_entry_baseline' not in cols:
        conn.execute("ALTER TABLE sandbox.e1_positions ADD COLUMN score_at_entry_baseline FLOAT")

The decay ratio should then use score_at_entry_baseline (if set) instead 
of ensemble_score for the denominator:
    decay_baseline = pos.get('score_at_entry_baseline') or pos.get('ensemble_score')
    decay_ratio = current_score / decay_baseline if decay_baseline > 0 else 1.0

## Acceptance Criteria
- Regime transition is detected when yesterday_regime != current_regime
- On transition day, decay veto is suppressed and baseline is reset
- The new baseline score is persisted to DB in score_at_entry_baseline
- Subsequent days use the new baseline for the decay ratio calculation
- The HOLD return on transition day does NOT suppress stop-loss or T2 evaluation 
  — those must still execute. Only the decay veto path is affected.
- Log message clearly states: old regime, new regime, old baseline, new baseline
- simulate mode: log what would happen without DB writes
```

---

---

## PROMPT S3-3 of 4 — F-10: Dollar-Weighted Decay Verdict Trigger
**Finding:** F-10 | Severity: 🟠 HIGH | File: `e1_reconciler.py`  
**Effort estimate:** 45 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Upgrade the score decay backfill verdict trigger in e1_reconciler.py to use 
a dollar-weighted threshold alongside the current binary comparison.

## The Bug
In backfill_decay_exit_verdicts(), the verdict logic is:
    verdict = 'VETO_COST_ALPHA' if p20 > exit_price else 'VETO_SAVED_CAPITAL'

A $0.01 gain (p20 = exit_price + 0.01) and a $50 gain both produce 
VETO_COST_ALPHA. The rate-based trigger treats them identically. This 
means the VETO_COST_ALPHA rate metric in the reconciler summary could be 
driven entirely by noise (near-zero counterfactuals), masking whether 
the decay veto is causing material alpha loss.

## The Fix

### Step 1: Categorize by materiality
Replace the binary verdict with a three-tier system:
    pnl_counterfactual_dollars = (p20 - exit_price) * shares  
    # Note: 'shares' must be fetched — add to the pending query

    if p20 <= exit_price:
        verdict = 'VETO_SAVED_CAPITAL'
    elif pnl_counterfactual_dollars > 100:
        verdict = 'VETO_COST_ALPHA'        # material alpha loss
    else:
        verdict = 'VETO_COST_ALPHA_NOISE'  # technically positive but immaterial

### Step 2: Fetch shares in the pending query
The current pending query is:
    SELECT id, ticker, exit_date, exit_price FROM sandbox.e1_decay_exit_tracking WHERE verdict IS NULL

Add shares to the SELECT:
    SELECT dt.id, dt.ticker, dt.exit_date, dt.exit_price, p.shares
    FROM sandbox.e1_decay_exit_tracking dt
    LEFT JOIN {config.E1_POSITIONS_TABLE} p ON dt.position_id = p.id
    WHERE dt.verdict IS NULL

If shares is NULL (position row deleted), fall back to a default of 100 shares 
for the materiality threshold calculation (this is conservative — 100 shares 
at >$1 move = >$100 threshold).

### Step 3: Update the decay audit summary
In the EOD portfolio summary (in e1_reconciler.py near "format_decay_audit_summary"), 
update the stats query to also count VETO_COST_ALPHA_NOISE separately:

    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN verdict = 'VETO_SAVED_CAPITAL' THEN 1 ELSE 0 END) as saved_count,
        SUM(CASE WHEN verdict = 'VETO_COST_ALPHA' THEN 1 ELSE 0 END) as cost_count,
        SUM(CASE WHEN verdict = 'VETO_COST_ALPHA_NOISE' THEN 1 ELSE 0 END) as noise_count,
        AVG(CASE WHEN verdict = 'VETO_COST_ALPHA' 
            THEN pnl_20d_counterfactual * exit_pnl_pct ELSE 0 END) as avg_cost_dollars
    FROM sandbox.e1_decay_exit_tracking WHERE verdict IS NOT NULL

## Acceptance Criteria
- Three verdict categories: VETO_SAVED_CAPITAL, VETO_COST_ALPHA, VETO_COST_ALPHA_NOISE
- VETO_COST_ALPHA requires counterfactual PnL > $100 (not just p20 > exit_price)
- VETO_COST_ALPHA_NOISE captures the technically-positive-but-immaterial cases
- shares is fetched in the pending query (with fallback to 100)
- The audit summary distinguishes material cost (VETO_COST_ALPHA) from noise
- The existing VETO_COST_ALPHA rate trigger for the decay threshold review 
  (in spec §8.3: >60% COST_ALPHA rate triggers upgrade review) should count 
  ONLY VETO_COST_ALPHA, NOT VETO_COST_ALPHA_NOISE
```

---

---

## PROMPT S3-4 of 4 — F-11: Positional Price Indexing Bounds Check in Reconciler
**Finding:** F-11 | Severity: 🟡 MEDIUM | File: `e1_reconciler.py`  
**Effort estimate:** 30 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Add a bounds check to the positional price indexing in backfill_decay_exit_verdicts() 
in e1_reconciler.py to handle delistings, trading halts, and data gaps that 
result in fewer than 20 rows in the LIMIT 20 query.

## The Bug
In backfill_decay_exit_verdicts(), after querying 20 post-exit prices:
    prices = conn.execute("""
        SELECT date, close FROM refined.price_history
        WHERE ticker = ? AND date > ?
        ORDER BY date ASC LIMIT 20
    """, [ticker, exit_date]).fetchall()

The code then accesses:
    p5  = prices[4][1]
    p10 = prices[9][1]
    p20 = prices[19][1]

The existing guard checks total history count (not the post-exit slice count):
    history_count = conn.execute("""
        SELECT COUNT(*) FROM refined.price_history
        WHERE ticker = ? AND date > ?
    """, [ticker, exit_date]).fetchone()[0]
    if history_count < 20:
        continue

The `history_count < 20` guard works in theory. But as the external reviewer 
identified: it checks total count of rows WHERE date > exit_date — however, 
the `LIMIT 20` query might return fewer rows if there are date gaps within the 
result set (e.g., a 5-day trading halt within the 20-day window). The count 
guard passes (e.g., 25 total rows) but the LIMIT 20 returns 20 rows with a 
gap, and `prices[19]` may actually be price at trading day 23 not 20.

More critically: if history_count is exactly 20 but a gap exists, prices[19] 
is the correct index but may be day 22 data. This is a silent data quality issue.

## The Fix

### Change 1: Replace the count guard with a len(prices) guard
After fetching `prices`, add an explicit length check:
    prices = conn.execute(...).fetchall()

    if len(prices) < 20:
        logger.debug(
            f"Skipping decay backfill for {ticker} (exit {exit_date}): "
            f"only {len(prices)} post-exit price rows available (need 20). "
            f"Possible delisting or data gap."
        )
        continue

### Change 2: Add a safe accessor helper
Replace direct indexing with a safe accessor:
    def safe_price(prices, idx):
        return prices[idx][1] if len(prices) > idx else None

    p5  = safe_price(prices, 4)
    p10 = safe_price(prices, 9)
    p20 = safe_price(prices, 19)

    if p5 is None or p10 is None or p20 is None:
        logger.warning(f"Incomplete price series for {ticker} after {exit_date}. Skipping verdict.")
        continue

### Change 3: Remove the pre-query history_count check
The `history_count = conn.execute(SELECT COUNT... WHERE date > ?)` pre-check 
is now redundant — delete it. The `len(prices) < 20` guard after fetching is 
the definitive check and avoids an extra DB round-trip.

## Acceptance Criteria
- Positional indexing is protected by a len(prices) >= 20 guard
- A safe_price() helper (or equivalent inline check) prevents IndexError
- The redundant pre-query COUNT check is removed
- Debug log when skipping due to insufficient rows
- Warning log when safe_price returns None (should not happen after len check, 
  but is a defensive backstop)
- No change to the verdict logic, counterfactual calculations, or DB updates
```

---

---

## Sprint Summary Reference

### Sprint 2 — Data Integrity & Shadow Validation

| Prompt | Finding | Severity | File | Est. Time |
|--------|---------|----------|------|-----------|
| S2-1 | F-03: Piotroski Yahoo PIT look-ahead | 🔴 CRITICAL | `piotroski.py` | 2 hrs + runtime |
| S2-2 | F-09: Consecutive window rule in monitor | 🔴 CRITICAL | `e1_monitor.py` | 30 min |
| S2-3 | F-05: Monitor string case + T2 match | 🟠 HIGH | `e1_monitor.py` | 30 min |
| S2-4 | F-17: Monitor sim_date + conn injection | 🟠 HIGH | `e1_monitor.py` | 30 min |
| S2-5 | F-20: Remove duplicate sector derivation | 🟠 HIGH | `e1_trader.py` | 15 min |
| S2-6 | F-16: Parameterized SQL (f-string sweep) | 🔴 CRITICAL | `e1_trader.py` | 3 hrs |
| S2-7 | F-18+19: High price alias + MTM fix | 🟡 MEDIUM | `e1_trader.py` (mock) | 2 hrs |
| S2-8 | F-12: Misc bundle (5 small fixes) | 🟡 MEDIUM | Multiple | 1.5 hrs |

**Recommended Sprint 2 commit sequence:**  
Commit A → S2-2 + S2-3 + S2-4 (all in e1_monitor.py, ~90 min)  
Commit B → S2-5 + S2-6 (e1_trader.py, high-value + systematic)  
Commit C → S2-1 (piotroski.py, isolated, followed by shadow wipe + rerun)  
Commit D → S2-7 + S2-8 (shadow fidelity + misc cleanup)

---

### Sprint 3 — Statistical Rigor & Governance

| Prompt | Finding | Severity | File | Est. Time |
|--------|---------|----------|------|-----------|
| S3-1 | F-06: Increase window to 60 sessions | 🟠 HIGH | `e1_monitor.py` | 1 hr |
| S3-2 | F-04: Regime transition baseline reset | 🟠 HIGH | `exit_evaluator.py` | 2 hrs |
| S3-3 | F-10: Dollar-weighted decay verdict | 🟠 HIGH | `e1_reconciler.py` | 45 min |
| S3-4 | F-11: Positional price bounds check | 🟡 MEDIUM | `e1_reconciler.py` | 30 min |

**Recommended Sprint 3 commit sequence:**  
Commit E → S3-1 + S3-4 (monitor + reconciler, low risk, quick wins)  
Commit F → S3-3 (dollar-weighted verdicts, isolated to reconciler)  
Commit G → S3-2 (regime transition — most complex, deserves its own PR)

---

## After All Sprints: Shadow Validation Checklist

Before promoting E1 V1.4 to the 60-session gate evaluation, verify:

1. **No duplicate trade log rows** — query: `SELECT position_id, COUNT(*) FROM e1_trade_log WHERE action='EXIT' GROUP BY 1 HAVING COUNT(*)>1` → should return zero rows
2. **days_held reflects trading days** — spot-check 5 positions: compare (exit_date - entry_date) calendar days vs stored days_held
3. **Credit veto stays ACTIVE in logs** — grep logs for "Credit veto disabled" → should never appear
4. **T2 detection captures STOP_VIOLATION exits separately** — query: `SELECT exit_trigger, COUNT(*) FROM e1_positions WHERE status='CLOSED' GROUP BY 1` and verify STOP_VIOLATION exits do NOT appear in the BE ratio numerator
5. **Shadow run uses sim_date throughout** — grep shadow logs for "date.today" calls → should be zero after patches
6. **Piotroski staleness in shadow logs shows reasonable values** — in a 2020 shadow run, staleness_days should be ~0-90, not ~2000
7. **No SQL syntax errors** — grep logs for "SyntaxError" or "Parser Error" → should be zero after parameterization
8. **Monitor consecutive-window escalation fires** — manually insert two consecutive WARNING rows into e1_performance_audit and run monitor → should produce CRITICAL + Telegram escalation
