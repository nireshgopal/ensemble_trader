# E1 V1.4 — Sprint 1 Implementation Prompts for Flash (Antigravity)
**Date:** May 3, 2026  
**Strategy:** E1 V1.4  
**Scope:** Sprint 1 — Capital Safety & Execution Integrity (6 findings: F-02, F-01, F-15, F-08, F-07, F-14)  
**How to use:** Paste each prompt individually into Flash on Antigravity. Each prompt is self-contained with all context Flash needs to produce a drop-in code patch.

---

## How to Use These Prompts

Each prompt below is structured as a **self-contained task for Flash**. It includes:
- The finding reference and severity
- The exact buggy code pattern (so Flash can locate it)
- The precise fix required
- Acceptance criteria Flash should verify before completing

Paste them **one at a time** in order. Each patch is independent and can be reviewed before moving to the next.

---

---

## PROMPT 1 of 6 — F-02: Reverse FRED Staleness Logic
**Finding:** F-02 | Severity: 🔴 CRITICAL | File: `e1_trader.py`  
**Effort estimate:** 30 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix a critical risk management bug in `e1_trader.py` where the S10 credit veto 
disables itself during market crises — the exact moment it must fire.

## The Bug
In the macro context loading block (search for "hydataage" or "credit_veto_active"), 
you will find this pattern:

    hy_data_age = (effectivedate - macrodate).days
    credit_veto_active = True
    if hy_data_age > 3:
        logger.warning(f"S10 HY Spread data is stale {hy_data_age} days old. Credit veto disabled.")
        credit_veto_active = False

This is backwards. FRED data lags 3+ days during rapid credit deterioration events 
(e.g., COVID crash April 2020 — confirmed gap of 4 days with VIX at 41.7). When the 
staleness guard fires, it disables the veto, allowing full-size entries exactly when 
the strategy should be blocking new positions.

## The Fix — Three-Part Remediation

Replace the block above with the following logic:

1. NEVER set credit_veto_active = False due to staleness. The last known HY value is 
   used as-is. Log a warning but keep the veto alive.

2. Add an "uncertainty premium": if data is stale AND hy_spread > 3.5, apply a 
   precautionary s10_scalar cap of 0.75. This accounts for the possibility that spreads 
   have widened further since the last known reading.

3. Add a real-time VIX proxy that operates independently of FRED freshness: if 
   vix_close > 35 at the time of the scan, cap s10_scalar at 0.50 regardless of 
   FRED state. VIX is real-time and always available.

The variable `s10_scalar` is computed later in the same function. Apply the caps 
before the normal veto evaluation block.

## Acceptance Criteria
- `credit_veto_active` is ALWAYS True after this block (staleness never disables it)
- A new boolean `stale_hy_data` is set to True when hy_data_age > 3
- When `stale_hy_data=True` AND `hy_spread > 3.5`, `s10_scalar` is capped at 0.75
- When `vix_close > 35`, `s10_scalar` is capped at 0.50 (independent path)
- Log messages clearly distinguish between: (a) stale data warning, (b) uncertainty 
  premium applied, (c) VIX circuit breaker triggered
- The original FRAGILE hard-exit logic (`manageonly = True`) is preserved unchanged
- The original CREDIT DANGER / CREDIT WATCH log messages are preserved unchanged
- No other logic in the function is modified

## Notes
- `s10_scalar` may not be defined yet at this point in the code. If so, initialize it 
  to 1.0 here and let the existing computation below override it normally — the caps 
  use `min(s10_scalar, X)` so they only reduce, never increase.
- The variable names `effectivedate`, `macrodate`, `vix_close`, `hy_spread`, 
  `current_regime` are all already in scope at this point in the function.
```

---

---

## PROMPT 2 of 6 — F-01: Delete Duplicate Trade Log INSERT on Exit
**Finding:** F-01 | Severity: 🔴 CRITICAL | File: `e1_trader.py`  
**Effort estimate:** 5 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix a duplicate database record bug in `e1_trader.py` where every trade exit 
produces two rows in the trade log table with mismatched PnL values.

## The Bug
In the exit evaluation loop (search for "INSERT INTO config.E1_TRADE_LOG_TABLE" 
near the SELL/exit handling block), there are TWO separate INSERT statements 
that both fire on a normal exit:

INSERT #1 — uses `mdata_dict['current_price']` as the price column, and includes 
the columns: position_id, ticker, action, trade_date, price, shares, dollar_value, 
trigger, reason, regime, pnl_pct, pnl_dollars, days_held

INSERT #2 — uses `exit_price` as the price column (a different value on days with 
quote latency), and includes: position_id, ticker, action, trade_date, price, shares, 
dollar_value, regime, trigger, pnl_pct, pnl_dollars, sim_run_id
(Note: INSERT #2 is MISSING days_held and uses a different price source)

This creates two rows per exit with different price and PnL values. All downstream 
metrics (T2 hit rate, BE ratio, time exit PnL averages in e1_monitor.py) are 
therefore double-counting exits and using inconsistent prices.

## The Fix

1. DELETE the second INSERT block entirely.

2. Merge what the second INSERT had that the first INSERT lacked:
   - Add `sim_run_id` to the first INSERT's column list and VALUES clause.
   - Confirm `days_held` is already in the first INSERT (it should be — if not, add it).
   - Use `exit_price` (not `mdata_dict['current_price']`) as the price in the 
     first INSERT, since `exit_price` is the authoritative filled price. 
     If `exit_price` is not yet assigned at that point, assign it from 
     `mdata_dict['current_price']` before the INSERT.

3. The final single INSERT should contain ALL of these columns:
   position_id, ticker, action, trade_date, price, shares, dollar_value,
   trigger, reason, regime, pnl_pct, pnl_dollars, days_held, sim_run_id

## Acceptance Criteria
- Only one INSERT per exit event in the trade log table
- The single INSERT uses `exit_price` as the price value
- The single INSERT includes both `days_held` and `sim_run_id`
- `sim_run_id` should be `f'{sim_run_id}'` if sim_run_id is set, else NULL
- No other exit logic is modified — only the log INSERT consolidation
- The STOP_VIOLATION emergency exit path is a separate code branch and should 
  be checked independently — it may have its own single INSERT which is correct 
  and should NOT be touched
```

---

---

## PROMPT 3 of 6 — F-15: Null-Safe ID Casting in Stop Promotion Branches
**Finding:** F-15 | Severity: 🔴 CRITICAL | File: `e1_trader.py`  
**Effort estimate:** 15 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix a crash bug in `e1_trader.py` where positions synchronized from the reconciler 
(with NULL/NA database IDs) cause a TypeError that kills the entire trader run 
when stop promotions are attempted.

## The Bug
In the exit evaluation loop, the EMERGENCY_EXIT path already has correct null-safe 
ID handling:

    pid = int(pos['id']) if pos.get('id') is not None and not pd.isna(pos.get('id')) else None

However, the ADVANCE_TO_BREAKEVEN and UPDATE_TRAILING_STOP branches use the raw 
variable `posid` (or `pos['id']` directly) in their UPDATE statements without this 
guard. SYNCHRONIZED rows inserted by the reconciler for positions found in Alpaca 
but missing in the DB use the string 'SYNCHRONIZED' as the dominant_cluster value 
and may have NULL IDs. When the exit evaluator returns ADVANCE_TO_BREAKEVEN or 
UPDATE_TRAILING_STOP for one of these rows, the raw `posid` is `<NA>`, and passing 
it to an f-string SQL query raises a TypeError that crashes the entire run — not 
just that one position.

## The Fix

At the TOP of the per-position processing block — before any branch evaluation 
(ADVANCE_TO_BREAKEVEN, UPDATE_TRAILING_STOP, SELL, etc.) — add a single unified 
null-safe ID assignment:

    pid = int(pos['id']) if pos.get('id') is not None and not pd.isna(pos.get('id')) else None

Then replace ALL uses of the raw `posid` or `pos['id']` in DB UPDATE/INSERT 
statements within that loop with `pid`. Every DB write should be guarded with:

    if not simulate and pid is not None:
        conn.execute(...)

This includes:
- ADVANCE_TO_BREAKEVEN branch UPDATE
- UPDATE_TRAILING_STOP branch UPDATE  
- All SELL/exit branch UPDATEs and INSERTs
- The HEAL_ABORT_NO_POSITION branch UPDATE

If `pid` is None (meaning the position has no valid DB ID), log a warning and 
skip the DB write — but still proceed with the Alpaca API call if applicable 
(e.g., still submit the market sell order even if the DB record can't be updated).

## Acceptance Criteria
- `pid` is defined exactly once, at the top of the position processing block
- All DB UPDATE/INSERT statements inside the loop use `pid`, not `posid` or `pos['id']`  
- Every DB write is guarded with `if not simulate and pid is not None:`
- If `pid` is None, a `logger.warning` is emitted specifying the ticker and the 
  operation that was skipped
- The existing null-safe pattern in the EMERGENCY_EXIT / stop-violation path is 
  NOT duplicated — the single top-of-loop definition replaces all per-branch 
  definitions
- No functional logic changes — only the ID safety pattern is standardized
```

---

---

## PROMPT 4 of 6 — F-08: Handle Unprotected Position After Failed Heal
**Finding:** F-08 | Severity: 🟠 HIGH | File: `e1_trader.py`  
**Effort estimate:** 45 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix a gap in `e1_trader.py` where a position can be left overnight with zero 
protective orders if the heal function fails — creating unlimited gap-down risk.

## The Bug
The `_heal_protection()` function (or `heal_protection()`) operates in sequence:
  Step 1: Cancels ALL existing orders for the ticker → position is now naked
  Step 2: Submits a new consolidated OCO bracket order → may fail
  Step 3: Returns "FAILED" on any non-emergency exception

In the main exit loop, when `heal_status` is checked, the "FAILED" return has 
no handler. The code continues past it, leaving the position open in Alpaca 
with no stop order and no limit order. If the stock gaps down overnight, there 
is no backstop.

Look for the heal_status check block. It currently handles:
  - "EMERGENCY_EXIT" → triggers liquidation logic ✓
  - "ABORTED" → marks DB as CLOSED ✓  
  - "HEALED" → continues normally ✓
  - None → continues normally ✓
  - "FAILED" → MISSING (falls through silently)

## The Fix

Add a handler for `heal_status == "FAILED"` immediately after the existing 
heal_status checks. The logic should be:

1. ATTEMPT FALLBACK: Try to submit a simple stop-only order (no take-profit leg). 
   This is simpler than an OCO and less likely to fail due to price validation errors.
   
   Use StopOrderRequest with:
   - symbol = ticker
   - qty = pos_shares (use the Alpaca-confirmed quantity if available from the 
     inventory guard inside _heal_protection, else use DB shares)
   - side = OrderSide.SELL
   - stop_price = round(pos.get('stop_loss'), 2) — use the DB stop_loss value
   - time_in_force = TimeInForce.GTC

2. IF stop_loss IS None OR math.isnan(stop_loss): 
   The position has no recoverable stop price. Trigger an EMERGENCY MARKET EXIT:
   - Submit MarketOrderRequest (SELL, GTC)
   - Send a Telegram alert: "🚨 EMERGENCY EXIT {ticker}: heal failed and no stop 
     price available. Position liquidated."
   - Log at CRITICAL level
   - Update DB: SET status='CLOSED', exit_trigger='HEAL_FAILED_EMERGENCY_EXIT'

3. IF the fallback stop submission ALSO fails (wrap in try/except):
   - Log at CRITICAL level: position is unprotected, manual intervention required
   - Send Telegram alert: "🚨 MANUAL REVIEW REQUIRED: {ticker} — heal failed AND 
     fallback stop failed. Position unprotected overnight."
   - Do NOT attempt to mark position as CLOSED (it's still open in Alpaca)
   - `continue` to move to next position

4. IF in simulate mode: log what would have happened and `continue`.

## Acceptance Criteria
- "FAILED" heal_status is explicitly handled — no silent fallthrough
- A fallback stop-only order is attempted before any emergency exit
- Emergency market exit only fires when stop_loss is unavailable
- Both paths (fallback stop and emergency exit) send a Telegram notification
- All paths call `continue` to prevent further processing of the same position
- Simulate mode logs the would-be action without API calls or DB writes
- The `_heal_protection()` function itself is NOT modified — only the caller's 
  handling of its return value is changed
```

---

---

## PROMPT 5 of 6 — F-07: Transactional Order Submission and DB Insert
**Finding:** F-07 | Severity: 🔴 CRITICAL | File: `e1_trader.py`  
**Effort estimate:** 30 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix an order-of-operations hazard in `e1_trader.py` where a live Alpaca bracket 
order can be submitted before its database record exists, creating an orphaned 
order that the reconciler cannot correctly manage.

## The Bug
In the entry execution block (search for "submit_order" near the new position 
entry path), the current sequence is:

  1. client.submit_order(LimitOrderRequest / MarketOrderRequest with bracket)  ← ORDER PLACED
  2. conn.execute(INSERT INTO config.E1_POSITIONS_TABLE ...)                   ← DB RECORD CREATED

If the INSERT fails for any reason (DuckDB lock contention, schema mismatch, 
disk issue), Alpaca holds a live bracket order with no corresponding DB record.

When the reconciler runs EOD, it finds the position in Alpaca but not in the DB 
and creates a placeholder row with a hardcoded 5% stop (`entry_price * 0.95`) 
instead of the ATR-calibrated 6× stop. This permanently misconfigures the 
position's risk management.

## The Fix — Compensating Transaction Pattern

Restructure the entry block to: INSERT FIRST, then submit ORDER, with rollback 
on order failure.

### Step 1: Move the DB INSERT before the order submission
The INSERT uses `RETURNING id` to get the new position ID. If this INSERT fails, 
log the error and `continue` to the next candidate — never submit the order.

### Step 2: Submit the order after a successful INSERT
Wrap `client.submit_order(...)` in try/except. If it raises any exception:
  a. Log at CRITICAL level: "ORDER SUBMISSION FAILED for {ticker} after DB insert. 
     Rolling back DB record."
  b. Execute: `conn.execute(f"DELETE FROM {config.E1_POSITIONS_TABLE} WHERE id = ?", [pos_id])`
     This removes the orphaned DB record.
  c. Send a Telegram notification: "⚠ Entry ORDER FAILED for {ticker}: {error}. 
     DB record rolled back. No position opened."
  d. `continue` to next candidate

### Step 3: Only after both succeed, write the fills table record and trade log entry
The existing conn.execute for the fills table (e1_fills) and the trade log INSERT 
should remain after the order submission, unchanged.

## Acceptance Criteria
- The DB INSERT (`RETURNING id`) executes BEFORE `client.submit_order()`
- If the INSERT fails: error is logged, `continue` is called, no order is submitted
- If the order submission fails: DB record is deleted (rolled back), Telegram alert 
  is sent, `continue` is called
- If both succeed: fills record and trade log entry proceed as before
- The `pos_id` / `sql_pos_id` variable assignment comes from the RETURNING clause 
  of the INSERT, not from a separate SELECT
- Simulate mode is unaffected — its path already skips DB writes and API calls
- No changes to the sizing logic, veto checks, or signal scoring that precede the 
  entry execution block
- DuckDB does not support multi-statement transactions in the same way as PostgreSQL. 
  Use the compensating DELETE pattern (not BEGIN/COMMIT) for the rollback.
```

---

---

## PROMPT 6 of 6 — F-14: Calendar Days vs. Trading Days in Time Exit
**Finding:** F-14 | Severity: 🟠 HIGH | File: `exit_evaluator.py` (called from `e1_trader.py`)  
**Effort estimate:** 60 minutes

---

```
You are working on Strategy E1 V1.4, an algorithmic trading system in Python.

## Task
Fix a systematic holding-period miscalculation where the strategy exits positions 
after ~14 trading sessions instead of the intended 20 trading sessions, suppressing 
T2 hit rates and distorting shadow run payoff profiles.

## The Bug
In the time exit evaluation logic (search for "days_held" or "TIMEEXIT20D" or 
"max_hold_days"), the holding period is computed as:

    days = (effectivedate - pd.to_datetime(pos['entry_date']).date()).days

This is CALENDAR days. The E1 Specification (§2, §5) explicitly defines the exit 
horizon as "20 TRADING DAYS" calibrated to the IC half-life of 22 sessions.

A 20-calendar-day window contains approximately 14 trading days (20 × 5/7). This 
means positions are being cut 30% earlier than the strategy intends, consistently 
exiting before the IC half-life window completes and before T2 targets have time 
to fill. DB evidence confirms: trades logged with days_held=20 had only 13 actual 
trading days elapsed.

## The Fix — Two-Part

### Part 1: Add a trading-day counter utility function

Add the following function to `exit_evaluator.py` (or wherever days_held is 
computed). It counts actual price_history rows between entry and as_of_date:

    def get_trading_days_held(conn, ticker: str, entry_date, as_of_date) -> int:
        """
        Count trading days between entry_date (exclusive) and as_of_date (inclusive)
        using the refined.price_history table as the authoritative trading calendar.
        """
        try:
            result = conn.execute("""
                SELECT COUNT(*) FROM refined.price_history
                WHERE ticker = ?
                  AND date > ?
                  AND date <= ?
            """, [ticker, entry_date, as_of_date]).fetchone()
            return result[0] if result else 0
        except Exception as e:
            # Fallback: approximate from calendar days (5/7 ratio)
            import logging
            logging.getLogger(__name__).warning(
                f"Trading day count failed for {ticker}: {e}. Using calendar approximation."
            )
            from datetime import date
            cal_days = (as_of_date - entry_date).days if hasattr(entry_date, 'days') else 0
            return int(cal_days * 5 / 7)

### Part 2: Replace the calendar-day calculation wherever days_held is used for 
the TIME_EXIT_20D trigger

Replace:
    days_held = (effectivedate - pd.to_datetime(pos['entry_date']).date()).days

With:
    days_held = get_trading_days_held(conn, ticker, pos['entry_date'], effectivedate)

The `conn` object must be passed into the evaluate() function (or the exit 
evaluator class) if it is not already available there. Add it as a parameter:
    def evaluate(pos, mdata_dict, regime, yesterday_regime, as_of_date, conn=None)

If `conn` is None (e.g., in unit tests), fall back to the calendar-day approximation 
using the same 5/7 ratio.

### What NOT to change
- The `days_held` value stored in the DB (in the UPDATE and INSERT statements in 
  e1_trader.py) should ALSO use trading days for consistency. Update those 
  assignments too if they use the old calendar formula.
- The `max_hold_days` config value (currently 20) represents TRADING days and 
  should NOT be changed. The fix is in the counter, not the threshold.
- The decay veto's "AND day_held > 5" check: verify whether this is calendar or 
  trading days and make it consistent with the new counter. It should be 
  trading days as well.

## Acceptance Criteria
- `get_trading_days_held()` function exists and queries `refined.price_history`
- The TIME_EXIT_20D trigger fires on trading day 20, not calendar day 20
- `days_held` stored in DB reflects trading days (consistent with the trigger)
- The fallback (when conn is None) uses 5/7 × calendar days approximation
- A unit-testable interface: `evaluate()` accepts an optional `conn` parameter
- Shadow mode passes `conn` to `evaluate()` — verify the call site in e1_trader.py 
  is updated to pass `conn`
- The fix does not alter any other exit trigger logic (STOP, T2, DECAY_VETO, 
  ALMANAC_EXIT) — only the time-exit day counter is changed
```

---

---

## Summary Reference

| Prompt | Finding | Severity | File | Estimated Effort |
|--------|---------|----------|------|-----------------|
| 1 | F-02: Reverse FRED staleness logic | 🔴 CRITICAL | `e1_trader.py` | 30 min |
| 2 | F-01: Delete duplicate trade log INSERT | 🔴 CRITICAL | `e1_trader.py` | 5 min |
| 3 | F-15: Null-safe ID casting in all branches | 🔴 CRITICAL | `e1_trader.py` | 15 min |
| 4 | F-08: Handle unprotected position after failed heal | 🟠 HIGH | `e1_trader.py` | 45 min |
| 5 | F-07: Transactional order + DB insert | 🔴 CRITICAL | `e1_trader.py` | 30 min |
| 6 | F-14: Calendar days → trading days in time exit | 🟠 HIGH | `exit_evaluator.py` | 60 min |

**Recommended commit sequence:**  
Commit 1 → Prompts 1 + 2 + 3 (all in e1_trader.py, low ambiguity, highest safety ROI)  
Commit 2 → Prompts 4 + 5 (execution integrity, same file)  
Commit 3 → Prompt 6 (holding period fix, separate file, isolated for validation)

**After Sprint 1 is complete:**  
Run a fresh shadow session for at least 5 trading days and verify:
- No duplicate rows in e1_trade_log for any single exit event
- `days_held` in DB reflects trading days (compare to entry/exit dates manually)
- Credit veto remains ACTIVE in the log even when HY data is stale
- No TypeError crashes on ADVANCE_TO_BREAKEVEN or UPDATE_TRAILING_STOP actions
- No unhandled "FAILED" heal status in trader logs
