# Strategy E1: Contextual Trade Estimator (CTE) V1.1  
**Shrunk Contextual Sizer — Design Specification & Rationale**

**Document Version**: 1.1  
**Status**: Phase 3 Research — Pre‑Activation  
**Date**: May 2026  
**Author**: Strategy E1 Research Team  

**Depends On**:  
- `E1_SPECIFICATION.md` §§6.1, 13, 14 (rolling monitors, exit DNA, statistical ground truths)  
- `E1_SIMULATION_AUDIT_REPORT_V1.4` (12‑year exit table and regime attribution)  
- `sandbox.e1positions` (2014–present shadow‑runner simulation runs)

---

## 1. Executive Summary

The **Contextual Trade Estimator (CTE)** is a *context‑aware sizing scalar* that uses historical simulation outcomes to slightly increase or decrease the size of trades that E1 is already going to take.

CTE answers one question:

> “Given trades in historically similar macro/regime context, how did Strategy E1 actually perform?”

CTE:

- **Is not** a second entry gate.  
- **Is not** a replacement for the ensemble score or S10.  
- **Is not** a machine learning model.  

It is a **shrunk conditional mean lookup** that:

- Uses **macro/regime context features** to define cells.  
- Computes **shrunk average PnL per cell** via empirical‑Bayes style shrinkage to avoid over‑believing small samples.  
- Maps that shrunk mean to a **bounded multiplier** in \([0.90, 1.10]\).  
- Focuses on **dynamic market state** (VIX Momentum, Regime Age) rather than static levels, ensuring the logic is strictly incremental to S10.
- Applies only after the existing conviction scalar and before S10, with a **hard combined scalar cap**.

CTE V1.1 explicitly acknowledges:

1. **Walk‑forward reality** — the 2014–2026 simulation history has been extensively used to design E1; its role is to propose contextual effects, not to certify them. Paper/live trading is the first real out‑of‑sample test.  
2. **Shrinkage necessity** — every cell mean is treated as a noisy estimate; we never fully trust a 30‑trade cell as “truth”.

CTE is only activated after **prospective logging in paper trading** demonstrates that its context buckets behave directionally as expected.

---

## 2. Problem Statement & Design Goals

### 2.1 What CTE Is Trying to Solve

E1’s ensemble score measures **ticker‑level signal quality**: how strongly the fundamental, momentum, and mean‑reversion signals agree at a given moment.

CTE measures **situational context quality**:

- Macro regime (HEALTHY / FRAGILE / BEAR)  
- Volatility environment (VIX bucket)  
- Strategy’s own realized performance in similar contexts (per‑bucket PnL)

The question becomes:

> “Given we already passed the 0.65 gate and all vetoes, how kind has this context historically been to E1’s entries?”

### 2.2 Design Goals

1. **Zero veto power** — CTE can never block a trade or override S10; it only scales risk.  
2. **Shrink, don’t snap** — no hard SUFFICIENT/INSUFFICIENT cut that flips from “ignore” to “fully trust” at 30 trades; instead, continuous shrinkage toward a global mean.  
3. **Distribution‑aware** — use average PnL as the primary signal so breakeven firewall and stop tails are reflected, not just T2 hits.  
4. **Walk‑forward humility** — treat the 2014–2026 simulation as proposal; only trust effects that survive prospective paper data.  
5. **Narrow influence** — keep the CTE multiplier tightly bounded and cap the total scalar product so CTE cannot materially increase portfolio‑level risk on its own.

---

## 3. Data & Scope

### 3.1 Training Dataset

CTE is trained on the **shadow‑runner gold set**:

```sql
SELECT *
FROM sandbox.e1positions
WHERE status = 'CLOSED'
  AND exit_trigger != 'ALPACA_SYNC_DESYNC'
  AND sim_run_id LIKE 'ANNUAL_AUDIT_%';
```

Key fields:

- `entry_regime` (HEALTHY / FRAGILE / BEAR)  
- `entry_date`, `days_held`  
- `exit_trigger`  
- `pnl_dollars`, `pnl_pct`  
- `dominant_cluster`, `cluster_dominance_pct` (from `e1_trader.py`)  
- VIX at entry (`vix_close` from `refined.macrodaily` join on `entry_date`)  

The 12‑year audit confirms approximate coverage:

- HEALTHY: 1,347 trades across 79 independent episodes  
- FRAGILE: 346 trades across 80 independent episodes  
- BEAR: 114 trades across 32 independent episodes  

### 3.2 Exclusions

The following are permanently excluded from CTE training:

- `ALPACA_SYNC_DESYNC` exits (reconciliation artifacts with positive truncation bias).  
- Any simulation runs with known spec drift or pre‑V1.4 logic.  
- Live/paper runs — these are reserved for validation, not training.

**Authoritative Training Set (Audit Gold):**
- **Filter**: `sim_run_id LIKE 'ANNUAL_AUDIT_%'`
- **Coverage**: 13 unique IDs (`ANNUAL_AUDIT_2014` through `ANNUAL_AUDIT_2026`).

---

## 4. Feature Set & IC Pre‑Filter

### 4.1 Base Features

CTE V1.1 focuses on the **trajectory and duration** of the market state:

1. **Entry Regime**  
   - Values: `HEALTHY`, `FRAGILE`, `BEAR`  

2. **VIX Momentum Bucket at Entry**  
   Measures the 20-day velocity of volatility to distinguish "Volatility Crushes" from "Spiking Panic."
   - Formula: \(\Delta V = \frac{VIX_\text{now} - VIX_\text{20d\_ago}}{VIX_\text{20d\_ago}}\)
   - Bins:
     - `VIX_COLLAPSING`: \(\Delta V < -0.20\)
     - `VIX_FALLING`: \(-0.20 \le \Delta V < -0.05\)
     - `VIX_STABLE`: \(-0.05 \le \Delta V < +0.05\)
     - `VIX_RISING`: \(+0.05 \le \Delta V < +0.20\)
     - `VIX_SPIKING`: \(\Delta V \ge +0.20\)

3. **Regime Age Bucket at Entry (F3 Candidate)**  
   Measures the duration of the current regime to distinguish "Fresh Recovery" from "Late-Cycle Fatigue."
   - Formula: Days since the last regime transition.
   - Bins:
     - `REGIME_FRESH`: \(age < 15\) days
     - `REGIME_ESTABLISHED`: \(15 \le age \le 90\) days
     - `REGIME_MATURE`: \(age > 90\) days

### 4.2 Information Coefficient (IC) Filter

Before building any lookup table, we test whether a feature actually explains realized PnL:

1. Compute **Spearman rank correlation (IC)** between each candidate feature and realized per-trade PnL, stratified by regime.  
2. If a feature’s IC magnitude is below a small threshold (e.g., \|IC\| < 0.03), that feature is **dropped**.
3. `regime_age_bucket` is the primary F3 candidate. `dominant_cluster` is the fallback if `regime_age` fails IC or shows insufficient coverage.

---

## 5. Cell Definition & Raw Statistics

### 5.1 Cell Key

A CTE cell is defined by:

```text
(entry_regime, vix_momentum_bucket, F3)
```

where `F3` is `regime_age_bucket` (primary) or `dominant_cluster` (secondary).

Maximum theoretical cells: 3 regimes × 5 VIX momentum buckets × 3 F3 buckets = **45 cells**.
In practice, many are empty (e.g., BEAR + VIX_COLLAPSING is rare) or absent due to IC filtering.

### 5.2 Raw Cell Metrics

For each cell we compute:

- `trade_count`: number of trades in the cell.  
- `episode_count`: number of distinct regime episodes represented in this cell.  
- `avg_pnl_dollars`: mean `pnl_dollars`.  
- `avg_pnl_pct`: mean `pnl_pct`.  
- `t2_hit_rate`: fraction of trades with `exit_trigger LIKE 'Target 2%'`.  
- `breakeven_rate`: fraction of trades that exit via breakeven stop.  
- `stop_rate`: fraction of trades that exit via initial/catastrophic stop.  
- `pnl_stddev`: standard deviation of `pnl_dollars`.  
- `avg_days_held`: mean `days_held`.

Global baselines are computed over the same training set:

- `global_avg_pnl_dollars`  
- `global_t2_rate`

These serve as priors for shrinkage.

---

## 6. Shrinkage Model (Empirical Bayes)

### 6.1 Motivation

The original binary SUFFICIENT/INSUFFICIENT rule treated a cell crossing 30 trades as “fully trustworthy” and anything below as “ignore entirely.” This is a crude surrogate for shrinkage and creates false confidence.

CTE V1.1 replaces this with a **continuous shrinkage estimator**:

- Small‑n cells are pulled strongly toward the global average.  
- Large‑n cells remain closer to their own empirical mean.

### 6.2 Shrunk PnL Formula

For each cell, define:

- \( n = \) `trade_count`  
- \( \hat{\mu}_\text{cell} = \) `avg_pnl_dollars`  
- \( \mu_\text{global} = \) `global_avg_pnl_dollars`  

Choose a shrinkage parameter \(k > 0\) controlling how fast we “trust” the cell (e.g., \(k \in [50, 100]\)).

Shrinkage weight:

\[
w = \frac{n}{n + k}
\]

Shrunk mean:

\[
\mu_\text{shrunk} = w \, \hat{\mu}_\text{cell} + (1 - w) \, \mu_\text{global}
\]

Properties:

- At very small n, \(w \approx 0\) and \(\mu_\text{shrunk} \approx \mu_\text{global}\).  
- As n grows large, \(w \to 1\) and \(\mu_\text{shrunk} \to \hat{\mu}_\text{cell}\).  

Optional: the same structure can be applied to T2 rate or other probabilities if used as secondary guards.

### 6.3 Data Quality Flag

Each cell receives a coarse quality flag:

- `data_quality = 'WEAK'` if:
  - `trade_count < n_min` (e.g., 10) **or**  
  - `episode_count < 3`.  

- `data_quality = 'MODERATE'` otherwise.

No cell is ever considered “fully sufficient”; confidence is continuous and implicit in \(w\). BEAR cells will typically have low n and episode_count; shrinkage will pull them close to the global mean, effectively neutralizing CTE in BEAR (as desired).

---

## 7. Mapping to CTE Multiplier

### 7.1 Ratio vs Global

CTE’s primary signal is the ratio of shrunk cell mean to global mean:

\[
r = \frac{\mu_\text{shrunk}}{\mu_\text{global}}
\]

Interpretation:

- \(r > 1\): historically better than average context.  
- \(r < 1\): historically worse than average context.

### 7.2 Multiplier Tiers

CTE multiplier:

```text
if data_quality == 'WEAK':
    cte_multiplier = 1.00
else:
    if r >= 1.30:              cte_multiplier = 1.10
    elif 1.10 <= r < 1.30:      cte_multiplier = 1.05
    elif 0.90 <= r < 1.10:      cte_multiplier = 1.00
    elif 0.70 <= r < 0.90:      cte_multiplier = 0.95
    else:                       cte_multiplier = 0.90
```

Constraints:

- Overall range is **tight**: `cte_multiplier ∈ [0.90, 1.10]`.  
- Cells with `data_quality = 'WEAK'` always get 1.00 (neutral) regardless of r.

### 7.3 Secondary Guards (Optional)

To prevent pathological cases:

- If the shrunk T2 rate for a cell is extremely low or stop_rate extremely high, cap `cte_multiplier` at ≤ 1.00 even if `r > 1`.  
- If a cell’s shrunk mean is dominated by a small number of outsized wins, consider further shrinkage or manual review.

---

## 8. Implementation: Schema & Storage

### 8.1 Lookup Table DDL

The CTE lookup table is stored in `sandbox.e1_cte_lookup` and must be rebuilt whenever the training set or shrinkage parameters change.

```sql
CREATE TABLE IF NOT EXISTS sandbox.e1_cte_lookup (
    entry_regime          VARCHAR,       -- HEALTHY / FRAGILE / BEAR
    vix_momentum_bucket   VARCHAR,       -- COLLAPSING to SPIKING
    regime_age_bucket     VARCHAR,       -- FRESH to MATURE
    trade_count           INTEGER,       -- Raw n
    episode_count         INTEGER,       -- Independent regime episodes
    raw_avg_pnl           FLOAT,         -- Unshrunk mean
    shrunk_avg_pnl        FLOAT,         -- Bayesian shrunk mean (primary signal)
    global_avg_pnl        FLOAT,         -- The prior used for shrinkage
    pnl_stddev            FLOAT,         -- Confidence interval driver
    t2_hit_rate           FLOAT,         -- Secondary guard metric
    data_quality          VARCHAR,       -- WEAK / MODERATE
    cte_multiplier        FLOAT,         -- Final [0.90, 1.10] scalar
    last_updated          TIMESTAMP,     -- Rebuild audit trail
    PRIMARY KEY (entry_regime, vix_momentum_bucket, regime_age_bucket)
);
```

---

## 9. Integration into Sizing Chain

### 9.1 Sizing Formula

The E1 sizing chain becomes:

\[
\text{final\_risk} =
\text{base\_risk} \times
\text{conviction\_scalar} \times
\text{cte\_multiplier} \times
\text{S10\_macro\_scalar}
\]

Where:

- `conviction_scalar ∈ [0.75, 1.25]` (unchanged)  
- `cte_multiplier ∈ [0.90, 1.10]` (new)  
- `S10_macro_scalar ∈ [0.0, 1.25]` (0 for credit veto, up to 1.25 for Panic Recovery)

### 9.2 Combined Scalar Cap

Define:

\[
s_\text{eff} = \text{conviction\_scalar} \times \text{cte\_multiplier} \times \text{S10\_macro\_scalar}
\]

Apply a hard cap:

\[
s_\text{capped} = \min \left( s_\text{eff}, \, 1.50 \right)
\]

Final risk:

\[
\text{final\_risk} = \text{base\_risk} \times s_\text{capped}
\]

This ensures that **no combination** of high conviction, favorable CTE, and Panic Recovery S10 can exceed 1.50× base risk.

### 9.3 Logging Requirements

On every new entry, the following must be logged to `sandbox.e1positions`:

- `cte_bucket` (serialized `(entry_regime, vix_bucket, F3)` key)  
- `cte_raw_avg_pnl`  
- `cte_shrunk_avg_pnl`  
- `cte_multiplier`  
- `scalar_pre_cap = conviction_scalar × cte_multiplier × S10_macro_scalar`  
- `scalar_post_cap = s_capped`

These fields enable future attribution and drift monitoring.

---

## 10. Operational Phases & Walk‑Forward Validation

### 10.1 Phase 0 — Offline Training

- Build initial CTE lookup table from simulation (`ANNUAL_AUDIT_%` runs).
- **Engineering Note**: Training SQL must utilize `LAG(vix_close, 20) OVER (ORDER BY date)` to compute VIX momentum and a cumulative count resetting on regime changes to compute regime age.
- No impact on live or paper sizing; this is an offline calibration step.

### 10.2 Phase 1 — Logging‑Only Paper Mode

For at least **60 paper trading sessions**:

- Compute all CTE values per entry (vix_momentum, regime_age, multiplier), log to DB.  
- **Do not** apply `cte_multiplier` to actual sizing — force `cte_multiplier = 1.00` in risk calculation.  
- Target: accumulate ~100–200 paper trades.

At the end of Phase 1:

- For each bucket, compare:
  - `cte_shrunk_avg_pnl` (training)  
  - `live_avg_pnl` (paper)  

Goal: directional sanity check (high‑CTE buckets should not systematically underperform low‑CTE buckets).

Phase 1 is explicitly **exploratory**, not a statistically powerful activation test.

### 10.3 Phase 2 — Controlled Activation

If Phase 1 shows no obvious inversion:

- Enable `cte_multiplier` **only for cells that**:
  - Have `data_quality != 'WEAK'` in training, and  
  - Have at least N_live trades in paper (e.g., 20+) with consistent sign (mean PnL not contradicting the shrunk direction).

Other cells remain at `cte_multiplier = 1.00` until they accumulate more evidence.

### 10.4 Ongoing Monitoring & De‑Activation

CTE is continuously monitored:

- If a bucket’s **live avg PnL** deteriorates materially vs `cte_shrunk_avg_pnl`, its multiplier should be shrunk toward 1.00 or frozen at 1.00 until re‑trained.  
- If high‑CTE buckets consistently underperform low‑CTE buckets, CTE should be globally de‑activated (all multipliers set to 1.00) and revisited.

CTE never becomes “set and forget”; it is a live hypothesis that must earn its risk budget.

---

## 11. Limitations & Honest Statements

1. **Single historical path**  
   The 2014–2026 dataset is one realized market path that has been used repeatedly in E1’s design. The 18.1% CAGR backtest is an optimistic upper bound, not a guaranteed baseline. Paper/live trading is the first true out‑of‑sample. CTE cannot change this and must prove its value in walk‑forward data.

2. **BEAR regime sparsity**  
   BEAR has few trades and episodes; shrinkage will pull most BEAR buckets toward the global mean. CTE multipliers in BEAR should be expected to hover near 1.00×, leaving BEAR risk control primarily to S10 and the ATR stop architecture.

3. **Feature instability**  
   `dominant_cluster` may fail the IC filter in some regimes (e.g., if nearly all HEALTHY trades are “Quality”). In such cases it will be dropped and replaced by better context features (e.g., rolling time‑exit PnL buckets, regime drift direction), which must themselves pass the IC filter.

4. **False confidence explicitly mitigated**  
   By design, CTE V1.1 avoids the “lookup table confidence trap”: no hard sufficiency threshold, continuous shrinkage, narrow multiplier bands, a hard scalar cap, and mandatory walk‑forward validation.

---

## 12. Summary

CTE V1.1 is a **shrunk, distribution‑aware, walk‑forward‑validated contextual sizer**:

- It uses macro/regime context to estimate conditional performance, but  
- It shrinks every estimate toward the global mean based on sample size and episodes, and  
- It influences sizing only within a tight band, with an explicit cap on total scalar product.

CTE does not change *what* Strategy E1 trades or *when* it trades. It makes position sizes **modestly smarter about context** — and only to the extent that both the simulation training and paper/live walk‑forward runs support that behavior.