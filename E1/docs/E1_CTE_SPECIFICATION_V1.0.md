# Strategy E1: Contextual Trade Estimator (CTE)
## Design Specification & Rationale
**Document Version**: 1.0  
**Status**: Phase 3 Research — Pre-Implementation  
**Date**: May 3, 2026  
**Author**: Strategy E1 Research Team  
**Depends On**: E1_SPECIFICATION.md §6.1, sandbox.e1positions (2014–present simulation run)

---

## 1. Executive Summary

This document specifies the design, rationale, and implementation plan for the **Contextual Trade Estimator (CTE)** — a lookup-table-based system that uses historical simulation data to provide a situational context score for every live trade entry.

The CTE is **not a second entry gate**. It is a **conditional expectation estimator** that answers one question:

> *"Of all the historical trades that entered in a similar macro/regime context, what actually happened?"*

The output is used solely as a **position sizing modifier** layered on top of the existing conviction scalar in `e1_sizer.py`. It cannot block a trade. It can make a trade slightly larger or slightly smaller based on how that contextual bucket has historically performed.

---

## 2. The Problem This Solves

Strategy E1's current ensemble score measures **signal quality** — how strongly the fundamental, momentum, and mean-reversion signals agree on a given ticker at a given moment.

What the ensemble score **cannot** measure is **situational context quality** — whether the current macro environment, regime state, and market microstructure are historically conducive to the T2 engine firing.

From the Phase 2 stress tests, we know this matters enormously:

| Year | Regime Context | T2 Hit Rate | Avg Trade PnL |
|---|---|---|---|
| 2020 HEALTHY | Post-crash recovery, VIX declining | ~19% | +$544 |
| 2020 FRAGILE | Mean-reversion bottom | ~47% | +$412 |
| 2022 HEALTHY | Bear-rally trap, VIX elevated | ~5% | −$101 |
| 2018 FRAGILE | Vol-shock recovery | ~30% | +$320 |

Two trades with identical ensemble scores of 0.85 — one entered in November 2020 HEALTHY, one entered in June 2022 HEALTHY — had dramatically different expected outcomes. The ensemble score is the same. The context is not. The CTE measures the context.

---

## 3. Why a Lookup Table, Not ML

This is the most important design decision in this document. The answer is not "ML is bad." The answer is that **the problem structure does not justify ML complexity at this stage**, and adding it would introduce risks that outweigh the potential gains.

### 3.1 The Degrees-of-Freedom Argument

A machine learning model's capacity for overfitting scales with its number of free parameters relative to the number of independent training observations.

Your simulation dataset, even spanning 2014–2026, does not contain thousands of independent regime-episodes. It contains approximately:

- 6–8 distinct bull market phases
- 3–4 genuine bear/correction phases
- 4–5 vol-shock episodes
- 2–3 grinding inflation/rate regimes

Each phase contains many trades, but those trades are **not independent** — they share macro context, sector behavior, and correlated outcomes. A 100-trade stretch in 2020 HEALTHY is not 100 independent data points for regime-learning purposes. It is closer to **1 independent regime-episode** observed 100 times.

If you train even a modest gradient-boosted model (say, 50 estimators, depth 4) on features derived from this data, you have potentially hundreds of free parameters being fit to what is effectively 15–25 independent regime-episodes. The overfitting is not a risk — it is a mathematical certainty.

A lookup table with 3 features × 3–4 bins each has approximately **27–64 cells**. Each cell is a simple conditional mean. There are **zero free parameters** beyond the binning boundaries, which are set by domain knowledge, not optimization. You cannot overfit a conditional mean.

### 3.2 The Non-Stationarity Argument

Financial data is non-stationary. The relationship between VIX level and T2 hit rate in 2015 may not hold in 2025. An ML model trained on 2014–2023 data will weight historical regime-episodes equally unless you explicitly implement time-weighting — which introduces its own parameter choices.

A lookup table is **transparent about this limitation**. When you look at a cell and see "T2 hit rate: 18% based on 47 trades from 2016–2018," you immediately know the sample is old and may not be representative. An ML model will give you a confident prediction with no such transparency.

### 3.3 The Interpretability Argument

Your §9 Intermediate Dump Rule states:

> *"The gap between the specification and the intermediate output is where the bugs live."*

A lookup table produces intermediate outputs you can audit by hand: "This trade is in the HEALTHY × ELEVATED_VIX × Quality bucket, which has historically had a 9% T2 hit rate across 38 trades." You can verify that in 30 seconds.

A gradient-boosted model's prediction for a single trade cannot be interpreted without SHAP values, which introduce another layer of tooling, potential bugs, and misinterpretation risk. The auditability you have worked to build into E1 would be degraded, not enhanced.

### 3.4 The Regime Coverage Argument

If you run the full 2014–present simulation and build a lookup table, some cells will be underpopulated (fewer than 30 trades). A lookup table handles this gracefully: mark those cells as "INSUFFICIENT_DATA" and fall back to the baseline scalar. An ML model will make confident predictions for those cells anyway, extrapolating from the nearest training examples. That extrapolation may be completely wrong.

### 3.5 The Upgrade Path Argument

Starting with a lookup table does not foreclose ML. It establishes a **validated baseline**. Once you have:

1. A lookup table running in paper trading for 60+ sessions
2. Live data confirming that the lookup table's conditional T2 rate estimates are directionally correct
3. A larger independent regime-episode dataset (requires several more market cycles)

...then a sparse linear model (logistic regression on interaction terms) is the appropriate next step. Not before.

**Summary**: Use a lookup table because it has zero free parameters, full auditability, graceful handling of underpopulated cells, and a clear upgrade path. Use ML when you have 50+ independent regime-episodes to train on and a validated baseline to compare against.

---

## 4. Feature Selection

The CTE uses exactly three features to define a "context bucket." These are chosen because:

1. They are **macro/regime-level**, not company-specific (avoids staleness across companies and years)
2. They are **available at entry time** without look-ahead
3. They are **causally linked** to T2 hit rate based on the Phase 2 attribution analysis

### Feature 1: Entry Regime
The E1 macro regime at the time of entry.
- Values: `HEALTHY`, `FRAGILE`, `BEAR`
- Source: `config.MARKET_REGIME_TABLE` at `entry_date`
- Rationale: The single strongest predictor of T2 hit rate in the Phase 2 analysis. FRAGILE 2020 had ~47% T2 rate; HEALTHY 2022 had ~5%.

### Feature 2: VIX Bucket
The VIX level at entry, binned into four categories.
- Bins:
  - `LOW`: VIX < 15 (calm bull market)
  - `NORMAL`: 15 ≤ VIX < 25 (baseline volatility)
  - `ELEVATED`: 25 ≤ VIX < 40 (stress/correction)
  - `PANIC`: VIX ≥ 40 (crisis)
- Source: `refined.macrodaily` `vix_close` at `entry_date`
- Rationale: VIX level directly affects ATR, which determines how far price needs to move to hit T2 (4× ATR). High VIX means wider ATR, which means T2 requires a larger absolute move. However, high VIX environments also produce larger moves — the relationship is non-linear, which is exactly why conditioning on it is valuable.

### Feature 3: Dominant Cluster
The dominant signal cluster driving the entry score.
- Values: `Quality`, `Trend`, `MeanReversion`
- Source: `dominant_cluster` column in `sandbox.e1positions`
- Rationale: From the 2020 attribution, T2 hit rate and average PnL differed significantly by cluster type. Mean-reversion cluster entries in FRAGILE 2020 had near-perfect T2 rates; trend cluster entries in HEALTHY 2022 were consistently stopped or time-exited at a loss.

### Features Explicitly Excluded

| Feature | Reason for Exclusion |
|---|---|
| Company-specific fundamentals (Piotroski, RS) | Stales rapidly across years; introduces company-level overfitting |
| Ensemble score level | Already used in conviction scalar; double-counting |
| Sector identity | Too granular — insufficient trades per sector per regime cell |
| HY spread | Highly correlated with regime; adding it creates near-empty cells |
| Price level, market cap | Proxy for era, not context; introduces temporal overfitting |

---

## 5. Lookup Table Structure

### 5.1 Cell Definition

Each cell is defined by a unique combination of:
- `entry_regime` × `vix_bucket` × `dominant_cluster`

Maximum possible cells: 3 × 4 × 3 = **36 cells**

In practice, many cells will never exist (e.g., PANIC VIX + HEALTHY regime is by definition contradictory and will be empty). Expected populated cells: 15–22.

### 5.2 Cell Contents

For each cell, store:

| Field | Type | Description |
|---|---|---|
| `trade_count` | INTEGER | Number of historical trades in this bucket |
| `t2_hit_rate` | FLOAT | % of trades that hit Target 2 |
| `avg_pnl_dollars` | FLOAT | Average net PnL per trade |
| `avg_pnl_pct` | FLOAT | Average % return per trade |
| `win_rate` | FLOAT | % of trades with pnl_dollars > 0 |
| `pnl_stddev` | FLOAT | Standard deviation of PnL (confidence measure) |
| `avg_days_held` | FLOAT | Average days to exit |
| `data_quality` | VARCHAR | `SUFFICIENT` (≥30 trades) or `INSUFFICIENT` (<30 trades) |
| `last_updated` | DATE | Date lookup table was last recomputed |
| `regime_episodes` | INTEGER | Estimated independent regime-episodes in this cell |

### 5.3 Minimum Trade Threshold

**A cell requires ≥ 30 trades to be considered `SUFFICIENT`.**

If a live trade enters a cell with `INSUFFICIENT` data, the CTE returns a neutral scalar (1.0) and logs the event. The trade proceeds normally. This is not a veto.

The 30-trade threshold is a minimum for a meaningful conditional mean. With fewer trades, the confidence interval around the T2 hit rate is too wide to justify any position sizing modification.

---

## 6. Sizing Modifier Logic

The CTE output is a **sizing multiplier** applied after the existing conviction scalar and before the S10 macro scalar.

### 6.1 Multiplier Mapping

Compare the cell's historical T2 hit rate to the strategy's overall baseline T2 hit rate (computed from all trades in the simulation set, ~17% from the 2020 analysis):

| Cell T2 Hit Rate vs Baseline | CTE Multiplier | Rationale |
|---|---|---|
| ≥ 1.5× baseline (e.g., ≥25%) | 1.15× | Strong historical context — size up modestly |
| 1.1× – 1.5× baseline (18–25%) | 1.05× | Slightly favorable — minor size-up |
| 0.7× – 1.1× baseline (12–18%) | 1.00× | Neutral — no adjustment |
| 0.4× – 0.7× baseline (7–12%) | 0.85× | Unfavorable context — size down |
| < 0.4× baseline (< 7%) | 0.75× | Poor historical context — significant size-down |
| INSUFFICIENT data | 1.00× | Neutral — insufficient data to adjust |

### 6.2 Integration into e1_sizer.py

The final position sizing formula becomes:

```
final_risk = base_risk × conviction_scalar × CTE_multiplier × S10_macro_scalar
```

The CTE multiplier is inserted between conviction scalar and S10 scalar. It cannot override the S10 macro scalar — macro risk controls always take precedence.

**Important constraints:**
- CTE multiplier is capped at [0.75, 1.15]. It cannot produce leverage or block trades.
- If the CTE table is unavailable (database error, stale data), default to 1.0 and log a warning.
- The CTE multiplier must be logged to `sandbox.e1positions` as `cte_multiplier` and `cte_bucket` on every entry for future attribution.

### 6.3 What the CTE Cannot Do

- It **cannot block a trade** that passes the 0.65 entry gate.
- It **cannot override the S10 credit veto**.
- It **cannot change the target or stop levels** (those are anchored to ATR at entry).
- It **cannot be used as justification for increasing overall portfolio risk** — the total portfolio risk cap still applies.

---

## 7. Implementation Plan

### Step 1: Build the Simulation Dataset (Pre-requisite)

Run the full 2014–present replay using the shadow runner with `sim_run_id` set to a dedicated identifier (e.g., `cte_training_v1`). This produces the complete `sandbox.e1positions` table with all trades, regimes, outcomes, and metadata.

**Required columns** in the simulation output:
- `entry_regime`, `dominant_cluster`, `exit_trigger`, `pnl_dollars`, `pnl_pct`, `days_held`, `stop_stage`
- VIX at entry (join from `refined.macrodaily` on `entry_date`)

### Step 2: Build the Lookup Table

```sql
CREATE TABLE IF NOT EXISTS sandbox.e1_cte_lookup (
    entry_regime     VARCHAR,
    vix_bucket       VARCHAR,
    dominant_cluster VARCHAR,
    trade_count      INTEGER,
    t2_hit_rate      FLOAT,
    avg_pnl_dollars  FLOAT,
    avg_pnl_pct      FLOAT,
    win_rate         FLOAT,
    pnl_stddev       FLOAT,
    avg_days_held    FLOAT,
    data_quality     VARCHAR,
    regime_episodes  INTEGER,
    baseline_t2_rate FLOAT,
    cte_multiplier   FLOAT,
    last_updated     DATE,
    PRIMARY KEY (entry_regime, vix_bucket, dominant_cluster)
);
```

### Step 3: Populate with the Core Query

```sql
WITH vix_bucketed AS (
    SELECT 
        p.*,
        CASE 
            WHEN m.vix_close < 15  THEN 'LOW'
            WHEN m.vix_close < 25  THEN 'NORMAL'
            WHEN m.vix_close < 40  THEN 'ELEVATED'
            ELSE 'PANIC'
        END AS vix_bucket
    FROM sandbox.e1positions p
    JOIN refined.macrodaily m 
        ON CAST(p.entry_date AS DATE) = m.date
    WHERE p.status = 'CLOSED'
      AND p.exit_trigger != 'ALPACA_SYNC_DESYNC'
      AND p.sim_run_id LIKE 'cte_training%'
),
baseline AS (
    SELECT AVG(CASE WHEN exit_trigger LIKE 'Target 2%' THEN 1.0 ELSE 0.0 END) AS baseline_t2
    FROM vix_bucketed
),
cell_stats AS (
    SELECT
        v.entry_regime,
        v.vix_bucket,
        v.dominant_cluster,
        COUNT(*)                                                          AS trade_count,
        AVG(CASE WHEN exit_trigger LIKE 'Target 2%' THEN 1.0 ELSE 0.0 END) AS t2_hit_rate,
        AVG(v.pnl_dollars)                                                AS avg_pnl_dollars,
        AVG(v.pnl_pct)                                                    AS avg_pnl_pct,
        AVG(CASE WHEN v.pnl_dollars > 0 THEN 1.0 ELSE 0.0 END)           AS win_rate,
        STDDEV(v.pnl_dollars)                                             AS pnl_stddev,
        AVG(v.days_held)                                                  AS avg_days_held,
        (SELECT baseline_t2 FROM baseline)                                AS baseline_t2_rate,
        CASE WHEN COUNT(*) >= 30 THEN 'SUFFICIENT' ELSE 'INSUFFICIENT' END AS data_quality
    FROM vix_bucketed v
    GROUP BY 1, 2, 3
)
SELECT
    *,
    CASE
        WHEN data_quality = 'INSUFFICIENT'            THEN 1.00
        WHEN t2_hit_rate >= baseline_t2_rate * 1.5    THEN 1.15
        WHEN t2_hit_rate >= baseline_t2_rate * 1.1    THEN 1.05
        WHEN t2_hit_rate >= baseline_t2_rate * 0.7    THEN 1.00
        WHEN t2_hit_rate >= baseline_t2_rate * 0.4    THEN 0.85
        ELSE 0.75
    END AS cte_multiplier,
    CURRENT_DATE AS last_updated
FROM cell_stats
ORDER BY entry_regime, vix_bucket, dominant_cluster;
```

### Step 4: Add CTE Lookup to e1_trader.py

Add a function `get_cte_multiplier(conn, entry_regime, vix_at_entry, dominant_cluster)` that:

1. Bins VIX into the appropriate bucket.
2. Queries `sandbox.e1_cte_lookup` for the matching cell.
3. Returns `cte_multiplier` if `data_quality = 'SUFFICIENT'`, else returns `1.0`.
4. Logs the bucket and multiplier to `sandbox.e1positions` on entry.

If the lookup table is unavailable or the query fails, default to `1.0` and log a `WARNING`. Never raise an exception that would block a trade.

### Step 5: Log the CTE Output

Add two columns to `sandbox.e1positions`:

```sql
ALTER TABLE sandbox.e1positions ADD COLUMN IF NOT EXISTS cte_bucket    VARCHAR;
ALTER TABLE sandbox.e1positions ADD COLUMN IF NOT EXISTS cte_multiplier FLOAT DEFAULT 1.0;
```

These columns allow future attribution analysis: "Did trades in high-CTE buckets actually outperform trades in low-CTE buckets in live trading?" If they don't, the CTE is not adding value and should be disabled.

### Step 6: Recalibration Schedule

The lookup table should be recomputed:
- **Monthly**: During paper trading, to incorporate new simulation data.
- **After any major market regime shift**: A 2022-style grinding bear that lasts 12+ months will change the distribution of outcomes and the lookup table should reflect that.
- **Never during live trading hours**: Recalibration is a pre-market task, not a real-time operation.

---

## 8. Validation Protocol

Before promoting the CTE to live sizing, validate it in paper trading as follows:

### 8.1 Prospective Validation (60 sessions)
During the first 60 paper trading sessions, log `cte_multiplier` and `cte_bucket` for every entry but **do not apply the multiplier to actual sizing**. Let the strategy run on its baseline sizing as validated.

At session 60, run this attribution query:
```sql
SELECT 
    cte_bucket,
    cte_multiplier,
    COUNT(*) as trades,
    AVG(CASE WHEN exit_trigger LIKE 'Target 2%' THEN 1.0 ELSE 0.0 END) as live_t2_rate,
    AVG(pnl_dollars) as live_avg_pnl
FROM sandbox.e1positions
WHERE sim_run_id = 'paper_v1'
GROUP BY 1, 2
ORDER BY cte_multiplier DESC;
```

**Pass condition**: Cells with `cte_multiplier > 1.0` should show live T2 hit rates meaningfully higher than cells with `cte_multiplier < 1.0`. If the ranking is inverted or random, the CTE is not capturing real signal and should not be activated.

### 8.2 Activation Gate
Activate the CTE sizing modifier only if:
- ≥ 60 paper trading sessions completed.
- At least 3 of the 5 highest-CTE buckets show live T2 rate above strategy baseline.
- At least 2 of the 3 lowest-CTE buckets show live T2 rate below strategy baseline.

If these conditions are not met, keep `cte_multiplier = 1.0` (frozen) and continue collecting data.

---

## 9. What This Is Not

To be explicit about the boundaries of this system:

- **Not a replacement for the ensemble score**: The ensemble score measures signal quality at the ticker level. The CTE measures macro/regime context. They are orthogonal and additive.
- **Not a machine learning model**: There are no learned parameters, no training/validation splits, no gradient descent. It is a conditional mean lookup with domain-knowledge bins.
- **Not a new entry gate**: A trade that passes the 0.65 threshold enters regardless of its CTE bucket. The CTE only adjusts size within the existing risk framework.
- **Not permanent**: If the 60-session validation does not confirm the CTE's predictive power in live trading, it is disabled. The base strategy continues unchanged.

---

## 10. Open Items

| Item | Status | Resolution Path |
|---|---|---|
| VIX bin boundaries | Provisional | Review after full 2014–present simulation run; adjust if any bin has < 50 total trades |
| `regime_episodes` estimation | Not yet automated | Manual review per cell during initial calibration |
| CTE interaction with S10 scalar | Designed but untested | Monitor `effective_risk = cte × s10` distribution in paper trading; flag if any combination produces > 1.5× base risk |
| Upgrade to sparse linear model | Deferred | Requires 50+ independent regime-episodes; schedule review after 2 years of live data |
| Separate CTE per regime transition direction | Research item | Entering HEALTHY from BEAR (recovery) vs. entering HEALTHY from extended bull may have different T2 distributions |

---

## 11. Summary

The Contextual Trade Estimator is a **zero-parameter, fully auditable, conditional mean lookup** that uses three domain-knowledge features (regime, VIX bucket, dominant cluster) to provide a sizing modifier based on how similar historical contexts have performed.

It is chosen over ML because:
1. The dataset contains ~15–25 independent regime-episodes, not thousands — ML would overfit with certainty.
2. A lookup table is fully auditable, matching E1's §9 governance standard.
3. Underpopulated cells fail safely (neutral scalar) rather than extrapolating.
4. It establishes a validated baseline before any model complexity is added.

The CTE does not change the strategy's core architecture. It makes the position sizing marginally smarter about context — and does so in a way that can be independently validated in live paper trading before any capital risk is taken on the estimator's predictions.

---

*End of Document — E1_CTE_SPECIFICATION_V1.0.md*
