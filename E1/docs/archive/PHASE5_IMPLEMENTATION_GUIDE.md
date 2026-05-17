# Phase 5 Implementation Guide
**Step-by-Step Build Instructions for Signal Expansion**

**Date:** 2026-05-16
**Prerequisite:** Read `E1 Strategy — Phase 5.md` (the "Bible") first. This document tells you *what to type and where*.
**Governance:** `RULES.md` Rule #1 applies. Verify before assuming. Propose before executing.

---

## Step 0: Environment Verification (Do Not Skip)

Before writing any code, run these three checks and **report findings**.

### Check 0A: Confirm Ghost Signals Are Zero-Weight
Open `docs/signal_weights.json` and verify:
- `sig_ma_crossover`: weight = 0.0 in ALL three regimes (HEALTHY, FRAGILE, BEAR) → ✅ Confirmed
- `sig_sector_momentum`: weight = 0.0 in ALL three regimes → ✅ Confirmed
- `sig_earnings_acceleration`: not present in JSON (defaults to 0.0 via code) → ✅ Confirmed

### Check 0B: Confirm Phase 5 Columns Exist in Database
Connect to `C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb` (read-only) and run:
```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'refined' AND table_name = 'daily_signals_ml'
  AND column_name IN ('ma_50','ma_150','ma_200','high_252d','low_252d',
                       'avg_volume_21d','avg_volume_63d',
                       'rs_vs_spy_252d_skip1m','rs_vs_spy_126d_skip1m')
```
**Expected:** All 9 columns present. → ✅ Added 2026-05-16

### Check 0C: Confirm Layer 4 Multiplier Application Order
Open `E1/core/signal_votes.py`, lines 468–493.
**Finding:** Layer 4 multipliers (sentiment ±10%, analyst ±10%, EPS revision ±10%) apply **post-aggregation** — they multiply the raw `score` after the IC-weighted sum, before the `[-1,+1] → [0,1]` rescale. → ✅ Confirmed

---

## Step 1: Signal Cleanup — Delete Ghost Signals

**Goal:** Remove three signals that carry 0% weight in all regimes. This reduces noise in IC attribution and simplifies the codebase.

### 1.1 Delete `sig_earnings_acceleration`

#### File: `E1/core/signal_votes.py`

**Action 1 — Remove from `CANONICAL_SIGNALS` (line 25–34):**
```python
# BEFORE (8 signals):
CANONICAL_SIGNALS = [
    'sig_ma_crossover',
    'sig_rs_3month',
    'sig_sector_momentum',
    'sig_ma_slope',
    'sig_rsi_oversold',
    'sig_drawdown_recovery',
    'sig_fundamental',
    'sig_earnings_acceleration',  # ← DELETE THIS LINE
]

# AFTER (7 signals):
CANONICAL_SIGNALS = [
    'sig_ma_crossover',
    'sig_rs_3month',
    'sig_sector_momentum',
    'sig_ma_slope',
    'sig_rsi_oversold',
    'sig_drawdown_recovery',
    'sig_fundamental',
]
```

**Action 2 — Remove from `CLUSTERS` (line 37–41):**
```python
# BEFORE:
CLUSTERS = {
    'trend':          ['sig_ma_crossover', 'sig_rs_3month', 'sig_sector_momentum', 'sig_ma_slope'],
    'mean_reversion': ['sig_rsi_oversold', 'sig_drawdown_recovery'],
    'quality':        ['sig_fundamental', 'sig_earnings_acceleration'],  # ← Remove sig_earnings_acceleration
}

# AFTER:
CLUSTERS = {
    'trend':          ['sig_ma_crossover', 'sig_rs_3month', 'sig_sector_momentum', 'sig_ma_slope'],
    'mean_reversion': ['sig_rsi_oversold', 'sig_drawdown_recovery'],
    'quality':        ['sig_fundamental'],
}
```

**Action 3 — Delete computation block (lines 359–368):**
Delete the entire `# S8: Earnings Acceleration` block from `compute_votes()`:
```python
# DELETE THIS ENTIRE BLOCK:
    # S8: Earnings Acceleration (Revision Momentum)
    revision_pct = row.get('eps_estimate_30d_change')
    days_to_earn = row.get('days_to_earnings')

    if pd.notna(revision_pct) and pd.notna(days_to_earn) and 0 <= days_to_earn <= 60:
        decay = 1.0 - (days_to_earn / 60.0)
        raw = (revision_pct / 100.0) * decay * 8.0
        votes['sig_earnings_acceleration'] = float(np.clip(raw, -1.0, 1.0))
    else:
        votes['sig_earnings_acceleration'] = 0.0
```

### 1.2 Delete `sig_ma_crossover`

**Verification:** Weight is 0.0 in all three regimes in `signal_weights.json`. → Confirmed.

#### File: `E1/core/signal_votes.py`

**Action 1 — Remove from `CANONICAL_SIGNALS`:** Delete the `'sig_ma_crossover'` line.

**Action 2 — Remove from `CLUSTERS`:** Remove `'sig_ma_crossover'` from the `trend` list.

**Action 3 — Delete computation block (lines 266–277):** Delete the entire `# S1: MA Crossover` block from `compute_votes()`.

> [!CAUTION]
> **KEEP the variable extraction block at lines 261–264:**
> ```python
> close = row.get('close_price')
> s20 = row.get('sma_20')
> s50 = row.get('sma_50')
> s200 = row.get('sma_200')
> ```
> The variable `s200` is reused by the **S5/S6 dampener** at line 312:
> ```python
> sma200_factor = max(sma200_floor, close / s200) if (_all_valid(close, s200) and s200 > 0) else 1.0
> ```
> Deleting these lines will crash the dampener. Only delete the `if/elif/else` block below them (lines 267–277).

### 1.3 Delete `sig_sector_momentum`

**Verification:** Weight is 0.0 in all three regimes in `signal_weights.json`. → Confirmed.

#### File: `E1/core/signal_votes.py`

**Action 1 — Remove from `CANONICAL_SIGNALS`:** Delete the `'sig_sector_momentum'` line.

**Action 2 — Remove from `CLUSTERS`:** Remove `'sig_sector_momentum'` from the `trend` list.

**Action 3 — Delete computation block (lines 286–299):** Delete the entire `# S3: Sector Momentum` block from `compute_votes()`.

### 1.4 Post-Cleanup State

After Step 1, `CANONICAL_SIGNALS` should contain exactly **5 signals**:
```python
CANONICAL_SIGNALS = [
    'sig_rs_3month',
    'sig_ma_slope',
    'sig_rsi_oversold',
    'sig_drawdown_recovery',
    'sig_fundamental',
]
```

And `CLUSTERS` should be:
```python
CLUSTERS = {
    'trend':          ['sig_rs_3month', 'sig_ma_slope'],
    'mean_reversion': ['sig_rsi_oversold', 'sig_drawdown_recovery'],
    'quality':        ['sig_fundamental'],
}
```

### 1.5 Update Downstream Files After Cleanup

These files reference the deleted signals and must be updated:

#### File: `E1/pipeline/log_ensemble_scores.py`

**Action 1 — `CREATE_TABLE_SQL` (line 38–64):** Remove columns:
- `sig_ma_crossover DOUBLE` (line 46)
- `sig_sector_momentum DOUBLE` (line 48)

**Action 2 — Results dict (lines 242–256):** Remove:
- `'sig_ma_crossover': round(votes.get('sig_ma_crossover', 0), 4),`
- `'sig_sector_momentum': round(votes.get('sig_sector_momentum', 0), 4),`

**Action 3 — Column list (lines 261–267):** Remove `"sig_ma_crossover"` and `"sig_sector_momentum"` from the `cols` list.

> [!WARNING]
> The `ensemble_daily_scores` table already exists with historical data containing these columns.
> **Do NOT drop existing columns.** They contain historical signal votes.
> Simply stop writing to them. The `CREATE TABLE IF NOT EXISTS` won't re-create.
> Use `ALTER TABLE ... DROP COLUMN` only after the full backtest validates Phase 5.

> [!IMPORTANT]
> **How `e1_trader.py` gets scores:** The trader does NOT call `compute_votes()` or `signal_votes.py` directly.
> It reads pre-computed `ensemble_score` from `refined.ensemble_daily_scores` via a JOIN (line 526–537 of `e1_trader.py`).
> This means Phase 5 signals will only affect trading **after** `log_ensemble_scores.py --rebuild` is run.
> Individual signal columns in the trader query (`sig_rs_3month`, etc.) are informational only — only `ensemble_score` drives entry decisions.

#### File: `E1/pipeline/compute_signal_ic.py`

**Action — `SIGNALS` list (lines 13–21):** Remove:
- `'sig_ma_crossover'`
- `'sig_sector_momentum'`

**Action — `CLUSTERS` dict (lines 24–28):** Remove same entries from `trend` list.

#### File: `E1/ops/momentum_logger.py`

**Action — `SIGNAL_COLS` list (line 9+):** Remove entries for the deleted signals.

#### File: `docs/signal_weights.json`

**Action:** Remove all `sig_ma_crossover`, `sig_sector_momentum` entries from each regime block. They are currently zero-weight but removing them keeps the JSON clean.

---

## Step 2: Add Five New Signals

**Goal:** Register 5 new always-on signals in the ensemble and implement their computation logic.

### 2.1 Register New Signals

#### File: `E1/core/signal_votes.py`

**Action 1 — Update `CANONICAL_SIGNALS`:**
```python
CANONICAL_SIGNALS = [
    # Existing (retained)
    'sig_rs_3month',
    'sig_ma_slope',
    'sig_rsi_oversold',
    'sig_drawdown_recovery',
    'sig_fundamental',
    # Phase 5 (new)
    'sig_rs_12month',
    'sig_rs_6month',
    'sig_price_stage',
    'sig_52w_high',
    'sig_volume',
]
```

**Action 2 — Update `CLUSTERS`:**
```python
CLUSTERS = {
    'trend':          ['sig_rs_3month', 'sig_ma_slope', 'sig_rs_12month', 'sig_rs_6month', 'sig_price_stage'],
    'mean_reversion': ['sig_rsi_oversold', 'sig_drawdown_recovery'],
    'quality':        ['sig_fundamental', 'sig_52w_high', 'sig_volume'],
}
```

> [!NOTE]
> Cluster assignment is tentative. The IC study may suggest moving `sig_52w_high` to `trend`.
> The key constraint is that cluster budgets are IC-proportional, so assignment affects weight distribution.

### 2.2 Implement Signal Computation Blocks

#### File: `E1/core/signal_votes.py` — inside `compute_votes()` function

Add the following blocks **after** the existing S7 (PEAD) block and **before** the `return votes` statement.

**Signal A — 12-Month RS (Skip Last Month):**
```python
    # S_A: 12-Month Relative Strength vs SPY (Skip Last Month)
    rs_12m = row.get('rs_vs_spy_252d_skip1m')
    if pd.notna(rs_12m):
        votes['sig_rs_12month'] = float(np.clip(rs_12m / 0.60, -1.0, 1.0))
    else:
        votes['sig_rs_12month'] = None
```

> [!IMPORTANT]
> The raw RS value is **pre-computed** in `refined.daily_signals_ml` as `rs_vs_spy_252d_skip1m`.
> The signal_votes function only normalizes it. The heavy math (lagged prices, SPY subtraction) was done in `build_ml_dataset.py`.

**Signal B — 6-Month RS (Skip Last Month):**
```python
    # S_B: 6-Month Relative Strength vs SPY (Skip Last Month)
    rs_6m = row.get('rs_vs_spy_126d_skip1m')
    if pd.notna(rs_6m):
        votes['sig_rs_6month'] = float(np.clip(rs_6m / 0.40, -1.0, 1.0))
    else:
        votes['sig_rs_6month'] = None
```

**Signal C — Price Stage (Minervini Trend Structure):**
```python
    # S_C: Price Stage (Minervini trend structure)
    ma50  = row.get('ma_50')
    ma150 = row.get('ma_150')
    ma200 = row.get('ma_200')
    if _all_valid(close, ma50, ma150, ma200):
        conditions = [
            close > ma50,
            close > ma150,
            close > ma200,
            ma50  > ma150,
            ma150 > ma200,
        ]
        stage_score = sum(conditions)  # 0 to 5
        votes['sig_price_stage'] = float((stage_score / 5.0) * 2 - 1)
    else:
        votes['sig_price_stage'] = None
```

> [!NOTE]
> `close` is already extracted at the top of `compute_votes()` as `row.get('close_price')`.
> `ma_50`, `ma_150`, `ma_200` are new columns in `daily_signals_ml` (added 2026-05-16).
> The `_all_valid()` helper is already defined at the bottom of this file.

**Signal D — 52-Week High Proximity:**
```python
    # S_D: 52-Week High Proximity
    high_252d = row.get('high_252d')
    low_252d  = row.get('low_252d')
    if _all_valid(close, high_252d, low_252d):
        range_52w = high_252d - low_252d
        if range_52w > 0:
            proximity = (close - low_252d) / range_52w
            votes['sig_52w_high'] = float((proximity * 2) - 1)
        else:
            votes['sig_52w_high'] = None
    else:
        votes['sig_52w_high'] = None
```

**Signal E — Volume Confirmation:**
```python
    # S_E: Volume Confirmation
    vol_21d = row.get('avg_volume_21d')
    vol_63d = row.get('avg_volume_63d')
    if _all_valid(vol_21d, vol_63d) and vol_63d > 0:
        vol_ratio_val = vol_21d / vol_63d
        votes['sig_volume'] = float(np.clip((vol_ratio_val - 1.0) / 1.0, -1.0, 1.0))
    else:
        votes['sig_volume'] = None
```

### 2.3 Column Name Mapping (DB → signal_votes.py)

This table ensures there is zero ambiguity about what `row.get('...')` maps to:

| `row.get()` key | DB Column in `daily_signals_ml` | Computed By |
|:---|:---|:---|
| `rs_vs_spy_252d_skip1m` | `rs_vs_spy_252d_skip1m` | `build_ml_dataset.py` tech_signals CTE |
| `rs_vs_spy_126d_skip1m` | `rs_vs_spy_126d_skip1m` | `build_ml_dataset.py` tech_signals CTE |
| `ma_50` | `ma_50` | `build_ml_dataset.py` tech_signals CTE |
| `ma_150` | `ma_150` | `build_ml_dataset.py` tech_signals CTE |
| `ma_200` | `ma_200` | `build_ml_dataset.py` tech_signals CTE |
| `high_252d` | `high_252d` | `build_ml_dataset.py` tech_signals CTE |
| `low_252d` | `low_252d` | `build_ml_dataset.py` tech_signals CTE |
| `avg_volume_21d` | `avg_volume_21d` | `build_ml_dataset.py` tech_signals CTE |
| `avg_volume_63d` | `avg_volume_63d` | `build_ml_dataset.py` tech_signals CTE |
| `close_price` | `close_price` | `build_ml_dataset.py` base_data CTE |

> [!IMPORTANT]
> **SSOT Rule:** All E1 signal code reads exclusively from `refined.daily_signals_ml`. Never query `refined.daily_signals` directly — that is an internal upstream table managed by the data pipeline (`pixel-data-feeds`).

> [!WARNING]
> **Duplicate Column Hazard:** `daily_signals_ml` currently contains legacy columns inherited from the upstream table alongside the new Phase 5 columns. Specifically:
> - `sma_50` (col 13, sparse/nullable) AND `ma_50` (col 46, fully populated) — **use `ma_50` for Signal C**
> - `sma_200` (col 14, sparse/nullable) AND `ma_200` (col 48, fully populated) — **use `ma_200` for Signal C**
> - `high_52w` (col 21, legacy) AND `high_252d` (col 49, Phase 5) — **use `high_252d` for Signal D**
> - The S5/S6 dampener currently reads `sma_200` via the `s200` variable (line 264). This still works because `sma_200` is present in `daily_signals_ml` via the base_data CTE, but should be migrated to `ma_200` in a future cleanup pass.

### 2.4 Update `__main__` Test Block

The `__main__` test block at the bottom of `signal_votes.py` (lines 508-528) will crash after Phase 5 because:
1. New signals return `None` (abstain), but the print formatter uses `{v:+.4f}` which crashes on `None`
2. The test row is missing all Phase 5 columns

**Action — Update the test block:**
```python
if __name__ == '__main__':
    regime_weights = load_regime_weights('docs/signal_weights.json')

    test_row = {
        'close_price': 150, 'sma_20': 145, 'sma_50': 140, 'sma_200': 130,
        'rs_vs_spy_63d': 5.0, 'sector_rank': 2, 'ma_slope_pct': 0.02,
        'volume': 1500000, 'vol_20d_avg': 1000000, 'rsi_14': 35, 'drawdown_52w': -0.15,
        'eps_surprise': 0.1, 'days_since_earnings': 10,
        'final_sentiment_factor': 0.3, 'eps_estimate_30d_change': 0.05,
        # Phase 5 columns
        'rs_vs_spy_252d_skip1m': 0.15, 'rs_vs_spy_126d_skip1m': 0.08,
        'ma_50': 145, 'ma_150': 138, 'ma_200': 130,
        'high_252d': 160, 'low_252d': 110,
        'avg_volume_21d': 1200000, 'avg_volume_63d': 1000000,
    }

    votes = compute_votes(test_row, breadth_pct=0.8)
    print(f"\nVotes (same across all regimes):")
    for k, v in sorted(votes.items()):
        if v is not None:
            print(f"  {k:35s} = {v:+.4f}")
        else:
            print(f"  {k:35s} = None (abstain)")

    for regime in ['HEALTHY', 'FRAGILE', 'BEAR']:
        test_row['regime'] = regime
        score = aggregate_score(votes, regime_weights, row=test_row)
        print(f"\n  [{regime:8s}] Ensemble Score: {score:.4f}")
```

### 2.4 Update Downstream Files for New Signals

#### File: `E1/pipeline/log_ensemble_scores.py`

**Action 1 — `CREATE_TABLE_SQL`:** Add columns for the 5 new signal votes:
```sql
    sig_rs_12month DOUBLE,
    sig_rs_6month DOUBLE,
    sig_price_stage DOUBLE,
    sig_52w_high DOUBLE,
    sig_volume DOUBLE,
```

**Action 2 — `_NEW_COLUMNS` list:** Add these same columns so `_ensure_columns()` auto-migrates:
```python
    ("sig_rs_12month", "DOUBLE"),
    ("sig_rs_6month", "DOUBLE"),
    ("sig_price_stage", "DOUBLE"),
    ("sig_52w_high", "DOUBLE"),
    ("sig_volume", "DOUBLE"),
```

**Action 3 — Results dict:** Add the new signal votes:
```python
    'sig_rs_12month': votes.get('sig_rs_12month'),
    'sig_rs_6month': votes.get('sig_rs_6month'),
    'sig_price_stage': votes.get('sig_price_stage'),
    'sig_52w_high': votes.get('sig_52w_high'),
    'sig_volume': votes.get('sig_volume'),
```

**Action 4 — Column list `cols`:** Add the 5 new signal names.

#### File: `E1/pipeline/compute_signal_ic.py`

**Action 1 — `SIGNALS` list (line 13):** Add:
```python
    'sig_rs_12month',
    'sig_rs_6month',
    'sig_price_stage',
    'sig_52w_high',
    'sig_volume',
```

**Action 2 — `CLUSTERS` dict (line 24):** Update to match `signal_votes.py`.

**Action 3 — SQL query (line 128):** Add the 5 new columns to the `SELECT` from `ensemble_daily_scores`.

#### File: `docs/signal_weights.json`

**Action:** Add entries for each new signal in all three regime blocks with `weight: 0.0` and `direction: 1`. This is a placeholder — the IC study will determine the actual weights.

---

## Step 3: Verification on Known Stocks

**Goal:** Prove each signal computes correctly before running the IC study.

### 3.1 Test Script

Create `scratch/verify_phase5_signals.py`:
```python
import duckdb
from E1.core.signal_votes import compute_votes
import pandas as pd

DB_PATH = r'C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb'
con = duckdb.connect(DB_PATH, read_only=True)

# Test: NVDA in mid-2023 (strong bull, should score high on A, B, C, D)
# Test: SPY in 2023 (RS vs itself should be ~0)
# Test: META in mid-2022 (downtrend, should score negative on A, B, C)
test_cases = [
    ('NVDA', '2023-07-15', 'Strong Bull — expect high A, B, C, D'),
    ('SPY',  '2023-07-15', 'RS vs self — expect A, B near zero'),
    ('META', '2022-07-15', 'Downtrend — expect negative A, B, C'),
]

for ticker, date, description in test_cases:
    row = con.execute(f"""
        SELECT * FROM refined.daily_signals_ml
        WHERE ticker = '{ticker}' AND date = '{date}'
    """).df()
    if row.empty:
        print(f"[SKIP] {ticker} {date}: No data")
        continue
    r = row.iloc[0].to_dict()
    votes = compute_votes(r)
    print(f"\n=== {ticker} {date}: {description} ===")
    for sig, vote in sorted(votes.items()):
        if vote is not None:
            print(f"  {sig:30s} = {vote:+.4f}")
        else:
            print(f"  {sig:30s} = None (abstain)")

con.close()
```

### 3.2 Expected Results

| Signal | NVDA Jul 2023 | SPY Jul 2023 | META Jul 2022 |
|:---|:---:|:---:|:---:|
| `sig_rs_12month` | High positive (~+0.8) | Near 0.0 | Strong negative |
| `sig_rs_6month` | High positive | Near 0.0 | Strong negative |
| `sig_price_stage` | +0.6 to +1.0 (4-5/5 conditions) | Moderate positive | Negative (-0.6 to -1.0) |
| `sig_52w_high` | Near +1.0 (at highs) | Moderate | Strong negative |
| `sig_volume` | Positive (accumulation) | Near 0.0 | Variable |

---

## Step 4: IC Study (2014–2024)

**Goal:** Determine which of the 5 new signals earn weight, and how much.

### 4.1 Rebuild Ensemble Scores

Before running the IC study, rebuild `ensemble_daily_scores` with the new signal columns populated:
```bash
uv run python E1/pipeline/log_ensemble_scores.py --rebuild --start 2014-01-02
```

> [!WARNING]
> This is a long-running operation (~2.5M rows). Budget 30+ minutes.

> [!CAUTION]
> **Sequencing matters:** `compute_signal_ic.py` reads signal votes from `ensemble_daily_scores`.
> You MUST update `compute_signal_ic.py` (Step 2.4 above) to include the 5 new signal columns in its SQL query
> **BEFORE** running this rebuild. Otherwise the IC study will have no data for the new signals.
> The rebuild populates the new columns in `ensemble_daily_scores`; the IC study then reads them.

### 4.2 Run IC Computation

```bash
uv run python E1/pipeline/compute_signal_ic.py
```

This will:
1. Compute Spearman IC for all 10 signals across 4 horizons (5d, 10d, 20d, 40d).
2. Compute regime-conditional IC (HEALTHY, FRAGILE, BEAR).
3. Compute cluster budgets.
4. Save results to `docs/ic_summary.csv`.
5. Generate `docs/signal_weights_CANDIDATE.json`.

### 4.3 IC Study Validation Checks

Before accepting the IC results:

1. **Threshold:** Any signal with IC < 0.03 should get 0% weight. This is the minimum bar.
2. **Pairwise correlation:** If `sig_rs_12month` and `sig_rs_6month` have correlation > 0.85, drop Signal B.
3. **Regime consistency:** Signals that pass IC in HEALTHY but fail in BEAR should NOT be added to BEAR weights.
4. **Volume signal:** If `sig_volume` IC < 0.02, drop it entirely (per Phase 5 Bible Section 3).

### 4.4 Output Artifacts

Save the following to `docs/`:
- `ic_study_phase5_YYYYMMDD.csv` — Full IC results
- `ic_correlation_matrix_phase5.csv` — Pairwise signal correlations
- `signal_weights_CANDIDATE.json` — Proposed new weights (do NOT overwrite production)

---

## Step 5: Update Production Weights

**Goal:** Promote candidate weights to production after IC validation.

### 5.1 Backup Current Weights
```bash
copy docs\signal_weights.json docs\signal_weights_v16_backup.json
```

### 5.2 Review Candidate Weights
Open `docs/signal_weights_CANDIDATE.json` and verify:
- HEALTHY weights sum to ~1.0
- FRAGILE/BEAR weights are **unchanged** from V1.6
- No single signal exceeds 50% (`MAX_SIGNAL_WEIGHT` cap in `signal_votes.py`)

### 5.3 Promote
```bash
copy docs\signal_weights_CANDIDATE.json docs\signal_weights.json
```

> [!IMPORTANT]
> `WEIGHTS_MODE` in `config.py` is currently `"frozen"`. To test experimental weights, temporarily change to `"experimental"` during the backtest, then revert to `"frozen"` for production.

---

## Step 6: Full Backtest Validation

**Goal:** Prove Phase 5 does not degrade the crown jewel (2022 bear defense).

### 6.1 Run Shadow Backtest
```bash
uv run python E1/testing/shadow_runner.py --start 2014-01-02 --end 2025-12-31 --reset
```

### 6.2 Validation Gates

| Metric | V1.6 Baseline | Phase 5 Minimum Gate | Why |
|:---|:---:|:---:|:---|
| **2022 Max Drawdown** | −8.48% | Must not worsen beyond −10% | Protect the core defensive edge |
| **2025 CAGR** | +15.90% | Must improve | This is the target year for Phase 5 |
| **Full Period Sharpe** | 1.13 | Must not decrease | Quality regression check |
| **Win Rate** | ~62% | Must stay above 60% | Statistical regression check |
| **2023 CAGR** | +23.42% | Must not decrease | Bull year capture check |

### 6.3 Threshold Sensitivity Check

> [!IMPORTANT]
> With 10 signals and better diversification, the score distribution will shift.
> Run a sensitivity analysis on the HEALTHY entry threshold (currently 0.72).
> Test: 0.68, 0.70, 0.72, 0.74, 0.76 — find the threshold that maximizes Sharpe without over-trading.

---

## Step 7: Definition of Done

- [ ] Ghost signals (`sig_earnings_acceleration`, `sig_ma_crossover`, `sig_sector_momentum`) deleted from all files.
- [ ] Five new signals implemented and registered in `CANONICAL_SIGNALS` and `CLUSTERS`.
- [ ] Signal verification passes on NVDA/SPY/META test cases.
- [ ] IC study completed; results saved to `docs/`.
- [ ] `signal_weights.json` updated with IC-derived weights.
- [ ] Full 2014–2025 backtest passes all validation gates.
- [ ] `WEIGHTS_MODE` reverted to `"frozen"` for production.
- [ ] `E1_SPECIFICATION.md` updated to reflect Phase 5 signal inventory.
- [ ] Paper account running for 60 sessions with no anomalies.

---

## Appendix A: File Change Summary

| File | Changes |
|:---|:---|
| `E1/core/signal_votes.py` | Delete 3 signals, add 5 signals, update `CANONICAL_SIGNALS`, `CLUSTERS`, `compute_votes()` |
| `E1/pipeline/log_ensemble_scores.py` | Update `CREATE_TABLE_SQL`, `_NEW_COLUMNS`, results dict, column list |
| `E1/pipeline/compute_signal_ic.py` | Update `SIGNALS` list, `CLUSTERS` dict, SQL query |
| `E1/ops/momentum_logger.py` | Update `SIGNAL_COLS` list |
| `docs/signal_weights.json` | Remove deleted signals, add new signals with 0.0 weight placeholder |
| `E1/docs/E1_SPECIFICATION.md` | Update signal table to reflect Phase 5 inventory |

## Appendix B: No-Touch Files

These files must NOT be modified during Phase 5:

| File | Reason |
|:---|:---|
| `E1/core/e1_trader.py` | Entry/exit logic unchanged |
| `E1/core/exit_evaluator.py` | Day 20/35 gate validated in V1.6 |
| `E1/core/e1_sizer.py` | Conviction scalar unchanged |
| `E1/core/config.py` | ATR multipliers, risk units unchanged (except `WEIGHTS_MODE` toggle for testing) |
| `E1/core/e1_reconciler.py` | Broker sync unchanged |
| `E1/testing/shadow_runner.py` | Backtest infrastructure unchanged |
| `E1/testing/mock_alpaca.py` | Mock broker unchanged |
