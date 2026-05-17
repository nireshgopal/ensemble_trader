# E1 Strategy — Phase 5 Signal Expansion Bible

**Date:** 2026-05-15  
**Author:** Perplexity Research  
**Status:** Approved for Implementation

---

## 1. Context: Where E1 Is Today

### What's Done and Locked
E1 V1.6 is live on paper trading and fully validated across multiple market cycles:

*   **2025 full-year**: +15.90% return, +2.12pp over V1.5 GOLD baseline.
*   **2022 bear market**: −8.48% floor vs SPY −18.1% — *this is the crown jewel result and must be preserved.*
*   **2026 YTD**: +12.25%, beating benchmark by +2.08%.
*   **Hold extension logic** (Day 20 → Day 35 gate) validated and contributing positive alpha.
*   **Fix A (PEAD abstention)**: `sig_fundamental = None` when stale. Validated in 2025 impact study (+2.5pp win rate improvement), approved and ready to ship to production.

### The One Confirmed Structural Problem
`sig_fundamental` is 100% PEAD with 50% weight in `HEALTHY` regime. PEAD decays to zero after 60 trading days and goes completely silent between earnings prints — meaning it is absent for roughly 70% of any given stock's trading days. This creates two cascading failures:

1.  **Failure 1 — Score compression**: When `sig_fundamental = 0.0` (pre-Fix A behavior), a name with perfect technicals scores approximately 0.44/1.0 raw — right at the 0.72 `HEALTHY` entry threshold, one bad signal away from being dropped.
2.  **Failure 2 — Secular blindness**: Even after Fix A, when PEAD abstains (`None`), a secular compounder like NVDA between earnings prints has no positive fundamental signal voting for it. It gets a neutral abstention, not a positive score. The rescued 18 trades from the Fix A study won more often (+2.5pp win rate) but returned less per trade (−35bp) because there was no forward signal pulling them — just the absence of a penalty.

> [!NOTE]
> Phase 5 solves this by adding always-on secular trend signals that give NVDA-type names a genuine positive score between earnings prints.

### What Phase 5 Does NOT Change
Before Flash touches anything, this is the **no-touch list**:

| Component | Status | Reason |
| :--- | :--- | :--- |
| **Piotroski F-score** | Global filter only, unchanged | Validated quality gate, not a signal |
| **Three-regime system** | `HEALTHY` / `FRAGILE` / `BEAR` | `FRAGILE` is the transition buffer that produced the 2022 result |
| **Exit evaluator logic** | Unchanged | Day 20/35 extension gate validated in 2025 backtest |
| **ATR stop multipliers** | Unchanged | Calibrated against 2014–2026 data |
| **Position cap** | Unchanged (4 positions) | Changing this alters risk profile unpredictably without its own backtest |
| **Conviction scalar** | Unchanged | Validated sizing behavior |
| **sig_rsi_oversold** | Unchanged | Regime-specific: zero in `HEALTHY`, 35% in `FRAGILE`/`BEAR` |
| **sig_drawdown_recovery** | Unchanged | Regime-specific: zero in `HEALTHY`, 27% in `FRAGILE`/`BEAR` |
| **sig_ma_slope** | Unchanged | Working signal with real IC, 18% weight in `HEALTHY` |
| **sig_rs_3month** | Unchanged | Working signal with real IC, 22% weight in `HEALTHY` |

---

## 2. What Phase 5 Changes

### Signals to Retire (Delete, Not Comment Out)

#### `sig_earnings_acceleration`
*   **Status**: Computed in `signal_votes.py`, stored in `ensemble_daily_scores`, carries 0.0 weight in all three regimes.
*   **Action**: Delete from `CANONICAL_SIGNALS`, remove computation block from `signal_votes.py`, archive the column in `ensemble_daily_scores` with a `_deprecated` suffix or drop it.
*   **Reason**: A ghost signal adds noise to IC attribution, makes the codebase harder to reason about, and contributes nothing to scoring.

#### `sig_ma_crossover`
*   **Status**: Computed, carries 0.0 weight in `HEALTHY`, unknown weight in `FRAGILE`/`BEAR`.
*   **Action**: If it carries zero weight in all regimes → delete. If it carries weight in `FRAGILE`/`BEAR` → keep but document clearly. Check `signal_weights.json` before deleting.

#### `sig_sector_momentum`
*   **Status**: Same as `sig_ma_crossover` — verify weights before deleting.
*   **Action**: Same rule applies.

> [!WARNING]
> Do not assume — verify the `weights` file first before deleting crossover or sector signals.

### Signals to Add (Phase 5 Core Work)
Five new signals. All computable from price and volume data. All designed to be always-on between earnings prints.

---

## 3. The Five New Signals

### Signal A: 12-Month Relative Strength vs SPY (Skip Last Month)
*   **Proposed `HEALTHY` weight**: 25–35% (IC study determines final)

This is the single most replicated finding in quantitative finance. Jegadeesh and Titman (1993) documented 12% annual outperformance from the strongest prior 6–12 month performers, replicated across 150 years and 40 countries. The "skip last month" adjustment is critical: the most recent 21 trading days are excluded because very short-term momentum mean-reverts. You want the durable 11-month trend, not the last spike.

**Why this is the anchor signal**: It never goes silent. NVDA at day 200 post-earnings still has a 12-month RS score. It captures exactly the secular compounders that PEAD missed between prints.

**Computation:**
```python
# S_A: 12-Month RS vs SPY, skip last month
# Requires: price_21d_ago, price_252d_ago, spy_21d_ago, spy_252d_ago
# Column in DB: rs_vs_spy_252d_skip1m

stock_return_12m = (price_21d_ago / price_252d_ago) - 1
spy_return_12m   = (spy_21d_ago   / spy_252d_ago)   - 1
rs_12m = stock_return_12m - spy_return_12m

# Normalize: clip to [-0.60, +0.60], scale to [-1, +1]
sig_rs_12month = float(np.clip(rs_12m / 0.60, -1.0, 1.0))

# Abstain if data unavailable
if pd.isna(rs_12m):
    votes['sig_rs_12month'] = None
else:
    votes['sig_rs_12month'] = sig_rs_12month
```

### Signal B: 6-Month Relative Strength vs SPY (Skip Last Month)
*   **Proposed `HEALTHY` weight**: 15–20% (IC study determines final)

Captures stocks that have recently accelerated vs SPY. Research shows 6-month and 12-month are correlated but not redundant — 6-month momentum captures breakout phases that the 12-month average smooths over.

> [!IMPORTANT]
> If the IC study shows pairwise correlation between Signal A and Signal B above 0.85, drop Signal B and give Signal A more weight. Measure the correlation; do not assume.

**Computation:**
```python
# S_B: 6-Month RS vs SPY, skip last month
# Requires: price_21d_ago, price_126d_ago, spy_21d_ago, spy_126d_ago
# Column in DB: rs_vs_spy_126d_skip1m

stock_return_6m = (price_21d_ago / price_126d_ago) - 1
spy_return_6m   = (spy_21d_ago   / spy_126d_ago)   - 1
rs_6m = stock_return_6m - spy_return_6m

# Normalize: clip to [-0.40, +0.40]
sig_rs_6month = float(np.clip(rs_6m / 0.40, -1.0, 1.0))

if pd.isna(rs_6m):
    votes['sig_rs_6month'] = None
else:
    votes['sig_rs_6month'] = sig_rs_6month
```

### Signal C: Price Stage (Trend Structure Quality)
*   **Proposed `HEALTHY` weight**: 15–20% (IC study determines final)

Adapted from Mark Minervini's Stage Analysis. A stock in a confirmed Stage 2 uptrend — price above 50-day MA, 50-day above 150-day, 150-day above 200-day — is in the condition institutional money accumulates. This is an ordinal signal: the more trend structure conditions are met, the higher the score.

**Computation:**
```python
# S_C: Price Stage (Minervini trend structure)
# Requires: close, ma_50, ma_150, ma_200

conditions = [
    close > ma_50,
    close > ma_150,
    close > ma_200,
    ma_50 > ma_150,   # Short MA above medium MA
    ma_150 > ma_200,  # Medium MA above long MA
]
stage_score = sum(conditions)  # 0 to 5

# Normalize to [-1, +1]: 5 conditions = +1.0, 0 conditions = -1.0
sig_price_stage = (stage_score / 5.0) * 2 - 1
votes['sig_price_stage'] = float(sig_price_stage)
```

### Signal D: 52-Week High Proximity
*   **Proposed `HEALTHY` weight**: 10–15% (IC study determines final)

Nearness to the 52-week high dominates raw past returns as a predictor of future returns (George and Hwang, 2004). Investors anchor to the 52-week high; a breakout suggests anchored sellers have been absorbed.

**Computation:**
```python
# S_D: 52-Week High Proximity
# Requires: close, high_252d, low_252d

high_52w = high_252d
low_52w  = low_252d
range_52w = high_52w - low_52w

if range_52w > 0:
    proximity = (close - low_52w) / range_52w  # 0 = at low, 1 = at high
    sig_52w_high = float((proximity * 2) - 1)   # Scale to [-1, +1]
    votes['sig_52w_high'] = sig_52w_high
else:
    votes['sig_52w_high'] = None
```

### Signal E: Volume Confirmation
*   **Proposed `HEALTHY` weight**: 5–10% (IC study determines final — may be zero)

Volume expansion during a price advance suggests institutional accumulation. This is the least academically validated signal. If the IC study shows IC < 0.02, drop it entirely.

**Computation:**
```python
# S_E: Volume Confirmation
# Requires: avg_volume_21d, avg_volume_63d

vol_recent   = avg_volume_21d
vol_baseline = avg_volume_63d

if vol_baseline and vol_baseline > 0:
    vol_ratio = vol_recent / vol_baseline
    # Normalize: 1.0 = neutral, 2.0 = doubled = +1.0, 0.5 = halved = -1.0
    sig_volume = float(np.clip((vol_ratio - 1.0) / 1.0, -1.0, 1.0))
    votes['sig_volume'] = sig_volume
else:
    votes['sig_volume'] = None
```

### PEAD: Demoted, Not Retired
`sig_fundamental` (PEAD) is not removed. Fix A has already made it abstain correctly when stale. In Phase 5, its weight will likely drop toward **15–22%** in `HEALTHY` as the new RS signals absorb its weight. It remains powerful for fresh earnings surprises (days 0–60) but will no longer dominate when absent.

---

## 4. Hypothesized `HEALTHY` Weights (Pre-IC Study)
*This is a starting hypothesis only. The IC study determines final weights. Do not hardcode these.*

| Signal | Current Weight | Phase 5 Hypothesis | Always On? |
| :--- | :---: | :---: | :---: |
| **sig_rs_12month** (new) | 0% | 28–32% | ✅ Yes |
| **sig_rs_6month** (new) | 0% | 15–20% | ✅ Yes |
| **sig_price_stage** (new) | 0% | 15–18% | ✅ Yes |
| **sig_52w_high** (new) | 0% | 10–13% | ✅ Yes |
| **sig_volume** (new) | 0% | 5–8% | ✅ Mostly |
| **sig_fundamental** (PEAD) | 50% | 15–22% | ❌ 60-day window |
| **sig_rs_3month** | 22% | 10–15% | ✅ Yes |
| **sig_ma_slope** | 18% | 10–15% | ✅ Yes |
| **sig_rsi_oversold** | small | unchanged | varies |

> [!NOTE]
> `FRAGILE` and `BEAR` regime weights are not touched in Phase 5. The bear-market entry signal mix was validated by the 2022 results.

---

## 5. The IC Study — How Flash Should Run It
*Weights come from this study, not from the hypothesis table above.*

### Dataset
*   **Period**: 2014–2024 (leave 2025 as OOS validation; 2026 as live test).
*   **Universe**: S&P 500 constituents (use point-in-time list to avoid survivorship bias).
*   **Frequency**: Daily signals, daily forward returns.
*   **Forward period**: 25 trading days (matching time exit cap).

### What to Compute Per Signal
1.  **IC (Spearman rank correlation)**: Between today's signal value and the 25-day forward return.
    *   **Threshold to earn weight**: IC > 0.03.
2.  **IC t-statistic**: `t = IC_mean / IC_std * sqrt(N_periods)`.
    *   **Threshold**: t > 2.0.
3.  **IC by year**: Compute annual IC separately. Consistency matters more than peak IC.
4.  **Pairwise correlation**: If any two signals have correlation > 0.85, keep the higher-IC one and drop the other.
5.  **IC by regime**: Run separately for `HEALTHY`, `FRAGILE`, and `BEAR`.

### Weight Assignment Rule
```text
weight_i = IC_i / sum(IC_all_passing_signals)
```
Run `recompute_weights.py` with the IC outputs. The IC output is the governance record.

---

## 6. Implementation Order for Flash

### Step 0 — Before Writing Any Signal Code
Verify columns exist in `refined.daily_signals_ml`:
*   `rs_vs_spy_252d_skip1m`
*   `rs_vs_spy_126d_skip1m`
*   `high_252d` / `low_252d`
*   `avg_volume_21d` / `avg_volume_63d`

### Step 1 — Signal Audit and Cleanup (Day 1)
*   Delete `sig_earnings_acceleration`.
*   Audit `sig_ma_crossover` and `sig_sector_momentum`.
*   Archive deprecated columns in `ensemble_daily_scores` (`_deprecated` suffix).

### Step 2 — Add Five New Signals (Days 2–3)
*   Add signals exactly as specified in Section 3.
*   Register in `CANONICAL_SIGNALS`.
*   Add tracking columns: `vote_s_rs12`, `vote_s_rs6`, `vote_s_stage`, `vote_s_52wh`, `vote_s_vol`.

### Step 3 — Verification on Known Stocks (Day 3)
Verify each signal computes correctly on 10 known stocks:
*   **NVDA in 2023**: Should score high on A, B, C, D.
*   **SPY itself in 2023**: RS signals should be near 0.
*   **Downtrend in 2022**: Should score negative on A, B, C.

### Step 4 — IC Study on 2014–2024 Data (Days 4–6)
*   Output IC table, t-statistic table, and correlation matrix.
*   Save to `docs/ic_study_phase5_YYYYMMDD.csv`.

### Step 5 — Update `signal_weights.json` (Day 7)
*   Backup `signal_weights.json` as `signal_weights_v16_backup.json`.
*   Run `recompute_weights.py`.

### Step 6 — Full Backtest Validation (Days 8–10)
| Metric | Minimum Gate | Why |
| :--- | :--- | :--- |
| **2022 max drawdown** | Must not worsen beyond −10% | Protect the core edge |
| **2025 CAGR** | Must improve vs 15.90% | Phase 5 target year |
| **Full period Sharpe** | Must not decrease vs V1.6 | Quality check |
| **Win rate** | Must stay above 60% | Regression check |

---

## 7. What "Done" Looks Like
*   Ghost signals deleted.
*   Five new signals verified on known stocks.
*   IC study completed and documented.
*   `signal_weights.json` updated.
*   2014–2025 backtest passes all validation gates.
*   Paper account running for 60 sessions with no anomalies.

---

## 8. The Single Most Important Thing to Preserve

> [!IMPORTANT]
> The governance process — **IC study → weights → backtest → validate → ship** — is not optional overhead. It is the reason your 2022 result exists. Any proposal that bypasses the IC study should be rejected. The process is the product. Flash should be instructed: no weight goes into `signal_weights.json` without a corresponding IC output to justify it.