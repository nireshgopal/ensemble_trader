# Strategy E1: Operational Protocol
**Read-First Deployment Manual**

---

## 1. Technical Architecture
| Role | Component |
| :--- | :--- |
| **Main Engine** | `e1_trader.py` |
| **Logic Brain** | `signal_weights.json` |
| **Exit Logic** | `exit_evaluator.py` |
| **Risk / Sizing** | `e1_sizer.py` |
| **DB Sync** | `e1_reconciler.py` |
| **Table (Sandbox)** | `sandbox.e1_positions` |

---

## 2. Daily Trading Routine (Mon-Fri)

### Phase 1: Pre-Market Macro (08:30 AM)
Run `spy_vol_skew_builder.py` and review the S10 Dashboard.
- **Check**: Is the `hy_spread` > 5.5? (If yes, entry veto is active).
- **Check**: Is VIX > 30? (If yes, aggressive 1.25x scaling is active).

### Phase 2: The 3:00 PM Window (Scanner & Entry)
Execution is automated via `run_0300pm_scanner.bat`.
1.  **RS Compute**: System fetches 3-month Sector RS to determine dynamic budgets (**L2 Promotion**).
2.  **Scanner**: Signals are scanned and ensemble scores computed with **Cluster Tagging**.
3.  **Filter**: The **0.65 threshold** (Healthy) or 0.30 (Bear) is applied.
4.  **Vetoes**: Almanac (Earnings < 5d) and Universal (Short Float/Price) filters run.
5.  **Entry**: Orders are submitted to Alpaca as **Consolidated Bracket Orders** (1 Target + 1 Stop).
6.  **Beta Sweep**: Near 3:45 PM, the system evaluates idle cash for a **Beta Sweep (L3)**. 
    *   *Note*: Only executes if `config.ENABLE_BETA_SWEEPER` is `True`.
7.  **Lifecycle**: Existing positions are checked for **Breakeven Progression (+1.5x ATR gain)** and **Score Decay (>40% drop)**.

### Phase 3: EOD Reconciliation & Audit (4:05 PM)
Run `run_405pm_eod.bat` (calls `e1_reconciler.py`).
- **Sync**: Ensures DuckDB `sandbox.e1_positions` matches the Alpaca brokerage state.
- **Budget Audit**: Review `sandbox.e1_sector_caps_history` to verify RS-leader budget scaling (L2).
- **Heal**: Restores missing brackets (Target + Stop) for any open positions.
- **Orphan Guard**: Identifies and closes any orders that didn't fill or brackets that became unlinked.

---

## 3. Weekly Maintenance (Sunday)
The signal weights must be refreshed weekly to stay aligned with market drift.
1.  **Scoring**: `log_ensemble_scores.py` (Full history refresh with **Cluster Logic**).
2.  **IC Compute**: `compute_signal_ic.py` (Rolling 12-year correlation audit).
3.  **Weight Gen**: `recompute_weights.py` (Outputs `signal_weights.json`).

> [!IMPORTANT]
> **Staleness Guard**: If `signal_weights.json` is > 7 trading days old, the trader will refuse to open new positions.

---

## 4. Troubleshooting & Safety

### 4.1 "Double-Sell" Protection
Before any market sell, the system calls `client.get_open_position`. If the brokerage reports 0 shares (due to a manual exit or stop hit), the DB record is closed without submitting a duplicate order.

### 4.2 Data Staleness Guard
The reconciler and trader will **abort** if price data in `refined.daily_signals_ml` is > 24 hours old. This prevents "ghost" stop-loss triggers based on old Friday data during a Monday morning run.

### 4.3 Dust Cleanup
Positions smaller than **$499** or holding $\le$ 4 shares are automatically flagged for "Dust Cleanup" to prevent portfolio fragmentation.

---

## 5. Master Audit Query (Decision Rationale)
Use this query to prove why a trade was taken and what weights were used at that exact moment:

```sql
SELECT 
    ticker, entry_date, entry_regime, 
    entry_score, dominant_cluster,
    cluster_dominance_pct as dominance,
    stop_loss, breakeven_trigger,
    printf('Score: %.2f | ATR: %.2f | Stop: %.2f', 
           entry_score, atr_at_entry, initial_stop) as logic_context
FROM sandbox.e1_positions
ORDER BY entry_date DESC;
```

---

## 6. Verification Benchmarks
Every 30 sessions, the live Sharpe must be compared against the **Backtest OOS Sharpe (1.13)**. 
- **Tracking**: If Live Sharpe $\ge$ **0.75**, strategy is within tolerance.
- **Audit**: If Live Sharpe < **0.45**, a formal parameter audit of the 0.65 threshold and signal weights is required.
- **Status**: Currently in **Paper Monitoring** Phase (60 sessions).

---

## 7. Shadow Mode Backtesting (`E1/testing/`)

The Shadow Mode framework runs the **full production pipeline** (trader + reconciler + all plumbing) against historical data using a mock Alpaca client and isolated simulation tables. This is the authoritative backtesting method for E1 — it tests both strategy performance and production plumbing.

### 7.1 Architecture
| Component | File | Purpose |
| :--- | :--- | :--- |
| **Mock Broker** | `E1/testing/mock_alpaca.py` | Replaces Alpaca API — records fills to `e1_sim_*` tables |
| **Sim Schema** | `E1/testing/sim_schema.sql` | Isolated `sandbox.e1_sim_*` tables mirroring production |
| **Runner** | `E1/testing/shadow_runner.py` | CLI orchestrator — feeds sim dates into live trader |

### 7.2 How to Run

**Single Day (Smoke Test / Plumbing Check)**
```bash
python E1/testing/shadow_runner.py --date 2026-03-15 --verbose
```

**Full Date Range (Backtest)**
```bash
python E1/testing/shadow_runner.py --start 2026-01-01 --end 2026-05-01
```

**Failure Injection (Plumbing Stress Test)**
```bash
python E1/testing/shadow_runner.py --date 2026-03-15 --inject zero-price-guard
python E1/testing/shadow_runner.py --date 2026-03-15 --inject oco-failure
python E1/testing/shadow_runner.py --date 2026-03-15 --inject staleness-guard
```

### 7.3 What Gets Tested
- ✅ **Full trader pipeline** — every line of `run_e1_trader()` and `run_e1_reconciler()` executes
- ✅ **OCO order submission** — healing pass, protection gaps, and API validation
- ✅ **Staleness guards** — freshness checks, schema migrations
- ✅ **DB writes** — all inserts/updates go to `sandbox.e1_sim_*` (production tables untouched)
- ✅ **Telegram notifications** — sent with `🧪 [SIM YYYY-MM-DD]` prefix (clearly marked)

### 7.4 Data Coverage (What is Replayable)
| Signal | Coverage | Notes |
| :--- | :--- | :--- |
| Price / Ensemble Scores / Regime | Since 2014 | Fully replayable |
| Financial Statements (F-Score) | Since 2012 | Point-in-time query (`report_date <= sim_date`) |
| Earnings Calendar | Since **2020** | Uses `dolt.earnings_calendar` (not `yahoo.earnings_calendar`) |
| Short Float Veto | From **2026-03-15** only | Treated as neutral before that date — slightly overestimates returns |
| News / Sentiment | N/A | Already baked into historical ensemble scores |

> [!IMPORTANT]
> Every shadow run prints a **Data Coverage Report** at startup. Always review it to understand the fidelity level of the simulation before interpreting results.

### 7.5 Interpreting Results
After a run, query the simulation tables directly:
```sql
-- P&L from simulation
SELECT ticker, entry_date, exit_date, pnl_dollars, exit_trigger
FROM sandbox.e1_sim_positions WHERE status = 'CLOSED' ORDER BY entry_date;

-- Equity curve
SELECT sim_date, portfolio_value, cash, open_positions
FROM sandbox.e1_sim_equity_curve ORDER BY sim_date;

-- Order / plumbing log
SELECT ticker, side, order_type, status, sim_date
FROM sandbox.e1_sim_order_log ORDER BY submitted_at;
```

### 7.6 Key Rules
- **Never run the shadow runner against production tables** — the runner redirects all config table names to `e1_sim_*` automatically.
- **Reset sim tables before each run** using the `--reset` flag to prevent stale position carryover between test runs.
- **Do not use shadow mode results to change strategy parameters** without also running the full plumbing stress test (Phase 2). Math results alone are insufficient.
