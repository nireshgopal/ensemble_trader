# Strategy E1: Unified Specification
**Single Source of Truth (SSOT)**

**Date**: May 1, 2026
**Status**: **PRODUCTION / LIVE**
**Version**: V1.4 (Phase 2 Hybrid)

## 1. Strategy Identity & DNA

**"Strategy E1 is a volatility-resilient breakout capture engine. It generates alpha by identifying high-conviction quality/momentum entries and harvesting them at 4x ATR Target 2. It is structurally anti-fragile to market shocks (2018: +11% relative to SPY; 2020: +52% relative to SPY) and provides modest bull-market alpha. Its primary vulnerability is sustained, low-volatility directional bear markets (2022: -15.7%), where regime misclassification causes time-exit drag in HEALTHY bear-rally traps. This risk is monitored via the Rolling Time Exit PnL threshold in §6.1 and managed via a regime-conditional time-exit shortening protocol."**

The strategy serves as a "Vol-Resilient Momentum Buffer," designed to capture idiosyncratic breakouts while maintaining high-resolution macro and fundamental vetoes.

## 2. System Philosophy
Strategy E1 is a **Quality-First Ensemble** (primarily PEAD-driven in Phase 1) designed to capture 20-day alpha while eliminating the "mechanical whipsaw" caused by tight 2.0x stops. 

### Core Governance Rules
- **The 0.65 Gate**: No trade is entered unless the ensemble score hits **0.65** (Healthy) or **0.60** (Bear).
- **The 20-Day Horizon**: The strategy exits primarily on time (20 trading days). This duration is based on the **IC Half-Life** observed in PEAD momentum signals, where predictive alpha significantly decays after 22 sessions. *Note: 15/25/30-day sensitivity testing is scheduled for Phase 3 Research.*
- **Circuit Breaker**: Every position is protected by a **6.0x ATR** hard stop-loss.
- **Breakeven Progression**: (ACTIVE) Stop advances to entry (+0.01) once price touches **+1.5x ATR** above entry.
- **Decay Veto**: (ACTIVE) Positions are exited early if the live ensemble score collapses by **40%** from the entry score. (Evidence: Test E showed that 50% decay conflicts with breakeven progression — the two controls interact and must be calibrated together).
- **Almanac Entry Veto**: (ACTIVE) No new entries allowed within **5 days** of a scheduled earnings announcement.
- **Almanac Exit Veto**: (ACTIVE) Force exit if Earnings within **2 days** AND position held $\ge$ 5 days.
- **Gap-Up Veto**: (ACTIVE) No new entries if Live Ask Quote is $>$ **4.0%** above previous close (Staleness Guard).
- **Bear Drawdown Veto**: (PROMOTED APRIL 30) In BEAR regimes, avoid entries with $>$ **65%** drawdown from 52-week high.
- **Sizing Source**: (CORRECTNESS) All sizing and level anchoring use **Live Alpaca Quotes** fetched at 3:00 PM.

---

## 2. Active Signal Weights (Regime-Aware)
Weights are derived from the latest Spearman Rank Correlation (IC) between signals and 20-day forward returns.

| Signal | HEALTHY Weight | FRAGILE Weight | BEAR Weight | Role |
| :--- | :---: | :---: | :---: | :--- |
| **Fundamental (S7)** | **50.0%** | 38.0% | 38.0% | The Quality Anchor |
| **3-Month RS (S2)** | 27.0% | 0.0% | 0.0% | Trend Confirmation |
| **MA Slope (S4)** | 23.0% | 0.0% | 0.0% | Momentum Filter |
| **RSI Bounce (S5)** | 0.0% | 35.0% | 35.0% | Panic Buyer |
| **DD Recovery (S6)** | 0.0% | 27.0% | 27.0% | Mean Reversion |

**Note**: In Healthy regimes, the strategy is 100% Quality/Momentum. In stress regimes, it pivots 100% to Quality/Mean-Reversion.

---

## 3. Risk & Sizing Architecture (S10)
Strategy E1 utilizes the **S10 Macro Framework** to scale exposure based on systematic stress.

### 3.1 Continuous Conviction Scalar
To eliminate "cliff effects," position sizing is scaled linearly based on the ensemble score:
- **Floor**: Score 0.60 -> 0.75x Scalar.
- **Ceiling**: Score 0.90 -> 1.25x Scalar.
- **Formula**: `0.75 + (score - 0.60) / 0.30 * 0.50`

### 3.2 Dynamic Macro Scaling (S10)
The standard position size is further multiplied by a scalar derived from VIX and Credit Spreads (HY).

| Scenario | Multiplier | Rationale |
| :--- | :--- | :--- |
| **Panic Recovery** (VIX > 30, HY < 5.5) | **1.25x** | Best historical expectancy bucket. |
| **Credit Stress** (HY > 4.5, BEAR) | **0.50x** | Systematic de-risking in high-stress regimes. |
| **Credit Veto** (HY > 5.5, FRAGILE) | **0.00x** | Hard stop — avoids systemic "Black Swan" events. |
| **Normal / Calm** | **1.00x** | Baseline allocation. |

### 3.3 Known Sizing Properties & Artifacts
- **Compounded Risk Logic**: The final risk unit is mathematically `Base Risk * Conviction Scalar * S10 Macro Scalar`. In stressed regimes (e.g., S10 = 0.50), the effective risk drops below half the baseline. *OOS verification for 2022-2026 must ensure enough stressed-regime sessions were modeled to validate this contraction.*
- **Volatility Exclusion Bias (The ATR Veto)**: Because the strategy enforces a 6.0x ATR stop-loss on a fixed 1.5% risk unit, any stock with an `ATR% > 5.0%` will structurally fail the 5% Dynamic Equity Floor. This means E1 V1.3 is intentionally biased against high-volatility names (e.g., high-beta tech or bios).
- **Share Flooring Artifact**: The final position size is computed via `math.floor(raw_shares)`. For high-priced, low-ATR stocks, this rounding can result in a final deployed dollar value that is slightly *below* the 5% equity floor, even though the unrounded value passed the gate. This is a mathematically sound artifact of whole-share routing.

---

## 4. Sector Budgeting (Dynamic RS-Aware Allocation)
Portfolio capital is allocated via a **Relative Strength (RS)** model to maximize leader concentration while maintaining defensive laggard floors.

| Sector Leadership | 3-Month RS vs SPY | Sector Budget | Rationale |
| :--- | :---: | :---: | :--- |
| **Leading** | $\ge$ 1.15 | **40.0%** | Maximize bull-market momentum capture. |
| **Neutral** | 0.85 - 1.14 | **21.0%** | Baseline governance allocation. |
| **Lagging** | $\le$ 0.85 | **10.5%** | Defensive throttling of underperformers. |

### 4.2 Governance-Locked Sector Budgets (V1.4 Expanded)
To ensure precise accounting and prevent "Other" category bloat, the following base budgets are locked in `config.py`:

| Sector | Base Budget | Ticker Overrides |
| :--- | :---: | :--- |
| **Technology** | 20.0% | |
| **Financial Services** | 15.0% | BRK.B |
| **Healthcare** | 12.0% | |
| **Consumer Discretionary** | 12.0% | |
| **Energy** | 10.0% | HAL |
| **Industrials** | 10.0% | GEV |
| **Basic Materials** | 8.0% | DOW |
| **Communication Services** | 8.0% | |
| **Utilities** | 5.0% | |
| **Real Estate** | 5.0% | |
| **Other / Miscellaneous** | 15.0% | |

### 4.3 Audit Requirement
All budget adjustments must be logged to `sandbox.e1_sector_caps_history` daily.

---

## 5. Exit Hierarchy (Order of Priority)
1.  **Circuit Breaker**: Exit immediately if Price hits **6.0x ATR** below entry.
2.  **Target T2**: 4.0x ATR above entry (Consolidated Automated Profit Taker).
3.  **Time Exit**: Mandatory 3:00 PM exit on the 20th trading day.
4.  **Decay Veto**: Exit if Score falls **>40%** from Entry Score AND Day Held > 5.
5.  **Almanac Entry Veto**: Avoid entry if Earnings < 5 days away.
6.  **Almanac Exit Veto**: Mandatory exit if Earnings < 2 days away AND held $\ge$ 5 days.
7.  **Gap-Up Veto**: Avoid entry if price has gapped up > 4.0% (preventing staleness).

**Note**: `e1_sizer.compute_entry_levels()` returns `t1_target` as a V1.3 artifact. This value is intentionally ignored by the trader. It is retained to avoid breaking the sizer's return contract.

---

| Benchmark | Period | Sharpe | CAGR | Role |
| :--- | :--- | :---: | :---: | :--- |
| **Backtest In-Sample** | 2014-2026 | **1.133**| 18.1% | **Live Tolerance Floor** |
| **Out-of-Sample (OOS)** | 2022-Present | **1.224**| 19.6% | Aspirational Benchmark |

**Clarification**: OOS Sharpe (1.224) exceeds the in-sample Sharpe (1.133). This is consistent with the 2022–2026 period being favorable for PEAD momentum strategies during the post-COVID recovery cycle. Note: 2022 contributed only 12 trades to the OOS sample under full position sizing (S10 scalar absent from backtest), introducing elevated variance in the OOS estimate. The **Backtest In-Sample (1.133)** is the primary floor for go-live decisions; the OOS figure is considered a "best-case" scenario for bull regimes.

**Phase 1 Paper Trading Gates & Definitions**:
- **Sharpe Ratio Definition**: All Sharpe ratios in this specification are computed on trade-level returns (PnL per trade / dollar_value at entry), not on calendar-time daily portfolio returns. A strategy that is 60–70% invested will report a higher trade-level Sharpe than an equivalent calendar-time Sharpe. The paper trading gate floors (0.75 / 0.85) are calibrated to trade-level Sharpe and must be compared against trade-level figures only.
- **Max Drawdown Definition**: Peak-to-Trough Equity Drawdown based on **EOD Close** prices. *Intraday drawdown may exceed this by 1–2% due to stop execution slippage.*
- **30-Session Sharpe Floor**: **$\ge$ 0.75** (Reduced from 0.80 due to S10 and veto divergences adding uncertainty).
- **60-Session Go-Live Gate**: **$\ge$ 0.85** (Calibrated against the in-sample Sharpe of 1.133. The OOS Sharpe of 1.224 is noted as an aspirational benchmark but should not be used as a baseline).
- **Live Sharpe < 0.45**: Audit 0.65 threshold and signal weights.
- **Max Drawdown Tolerance**: **$\le$ -20%** (The 19.8% backtest CAGR is an upper bound; live trading should experience shallower drawdowns due to S10 macro scaling).

**Note on FRAGILE Regime**: The 2020 FRAGILE regime attribution (17 trades, 100% win rate) reflects entries made at or near the bottom of the fastest equity market crash in modern history. These numbers are not representative of typical FRAGILE regime performance. A 2022 grinding bear FRAGILE-entry attribution is required before drawing conclusions about the FRAGILE signal mix's general reliability. This is a scheduled Phase 3 validation task.

### 6.1 Rolling Vital Signs (60-Session Window)

The Alpha Audit Engine (`e1_monitor.py`) computes these metrics every EOD to ensure the live portfolio aligns with the V1.4 Payoff Profile. A consecutive-window escalation rule is enforced: two sequential windows in WARNING state automatically trigger a CRITICAL escalation.

| Metric | Target | Warning Threshold | Action |
| :--- | :--- | :--- | :--- |
| **T2 Hit Rate** | $\ge$ 15% | < 10% (2 consecutive) | Full Weight & Threshold Audit |
| **BE Stop Ratio** | $\ge$ 75% | < 50% | T1/T2 Distance Review |
| **Time Exit PnL (HEALTHY)** | $\ge$ -$20/trade | < -$50/trade (2 consecutive) | Shorten Time-Exit to 12 days |
| **Time Exit PnL (BEAR)** | $\ge$ -$80/trade | < -$110/trade | Acceptable (2022 Baseline) |

#### **Rolling Time Exit PnL Monitor (Regime-Conditional)**

*   **In HEALTHY regime**: A rolling 20-session avg Time Exit PnL below **-$50/trade** is the primary early warning for "bear market rally trap" misclassification.
*   **In BEAR regime**: Time Exit losses up to **-$110/trade** avg are within tolerance (2022 empirical). The S10 scalar is already reducing size; time-exit drag is expected and acceptable.
*   **Action Trigger**: Two consecutive 60-session windows with HEALTHY Time Exit avg below **-$50** → shorten time exit horizon from **20 days to 12 days** for HEALTHY regime entries only until regime stabilizes.

---

## 7. Known Backtest Divergences & Phase 2 Priorities
The legacy `run_v13_backtest.py` script duplicates logic rather than importing the production modules. This caused several protective measures to be absent from the backtest. The 19.8% CAGR is a credible baseline, but should be treated as **$\pm$ 2%** given these divergences:

| Divergence | Direction of Bias | Impact on 19.8% CAGR | Status |
| :--- | :--- | :--- | :--- |
| **S10 Macro Scalar Absent** | Overstates stressed returns | Slight overstatement | Documented |
| **Gap-Up Veto Absent** | Mixed (Veto hurts returns) | Slight understatement | Documented |
| **Almanac Exit Veto Absent**| Holds through earnings | Mixed | Documented |
| **Sector Cap (Fixed)** | Was absent | Resolved | Harmonized |
| **Floor Formula (Fixed)** | Was miscalibrated | Resolved | Harmonized |

**The One Engineering Priority for Phase 2 — RESOLVED (May 2, 2026)**:
The backtest script has been superseded by the **Shadow Mode Framework** (`E1/testing/`). The shadow runner calls `run_e1_trader()` and `run_e1_reconciler()` directly — no logic duplication. All divergences in the table above are now addressed by the production-identical pipeline. See `E1_OPERATIONAL_PROTOCOL.md` Section 7 for operating instructions.
---

## 8. Known Open Items (Finalized by Evidence)

| Item | Status | Verdict Date |
|---|---|---|
| **S3 Sector Rank** | Deferred — no IC data collected yet | Phase 2 Research |
| **S7 IC shrinkage (50% cap)** | Deferred — accepted risk for paper | Before live capital |
| **T1 Partial Exits** | **REJECTED BY EVIDENCE** | April 28, 2026 |
| **BREAKEVEN Progression**| **ACTIVE (Promoted from Research)**| April 28, 2026 |
| **40% Decay Threshold** | **CONFIRMED BY EVIDENCE** | April 28, 2026 |

### 8.1 T1 Partial Exits (Rejected)
*   **Reasoning**: Backtesting revealed a 23% order conflict rate when attempting to manage both trailing stops and T1 limit trims simultaneously through Alpaca's current non-atomic bracket order implementation.
*   **Production State**: **Consolidated 100% Bracket Order**. The system submits a single order with Target T2 (+4x ATR) and Stop 6x ATR. 
*   **Primary Exit**: 20-Day Time Horizon remains the primary exit path.

### 8.2 S7 IC Shrinkage (Deferred with Trigger)
*   **Status**: Accepted risk for paper trading. 50% weight cap remains in place for now.
*   **Monitoring & Trigger**: The `ic_history` table must log S7 IC daily. The reconciler EOD run must compute and store the rolling 60-day S7 IC. An automated alert fires if the 60-day IC drops below 0.035 for 5 consecutive sessions — operator review required. Mandatory weight cap review if below threshold for 20 consecutive sessions.

### 8.3 Score Decay Tracking (Phase 2 Hardening)
*   **Status**: Phase 2 post-exit tracking is active from paper trading session 1. The 40% threshold will be reviewed after 60 sessions using `sandbox.e1_decay_exit_tracking`. 
*   **Review Trigger**: An upgrade to the 50% threshold will be scheduled if the `VETO_COST_ALPHA` rate exceeds 60% of tracked exits. To prevent noise from driving policy, a dollar-weighted materiality check is enforced: an exit is only classified as `VETO_COST_ALPHA` if the counterfactual opportunity cost is greater than $100. Technically positive but immaterial exits are binned as `VETO_COST_ALPHA_NOISE` and do not count toward the 60% review trigger.


## 9. Operational Workflow (The "Intermediate Dump" Rule)
Because Strategy E1 involves multiple compounding scalars, hard floors, and sector budgets, the final output cannot always be verified by eye. 

**Mandatory Pre-Flight Protocol**:
Before promoting *any* configuration change (e.g., floor adjustments, risk % changes, sector budget tweaks), the operator MUST:
1. Generate an "Intermediate Value Dump" for 3–5 representative tickers.
2. Output the exact values for: `raw_score`, `conviction_scalar`, `risk_dollars`, `atr_dollars`, `stop_distance`, `raw_shares`, `floored_shares`, and `final_position_size`.
3. Verify the math by hand.
4. *Limit to 5 tickers* to prevent context/looping exhaustion in automated logs.

The gap between the specification and the intermediate dump is where the bugs live.

---

## 10. Shadow Rule Governance (Promoted April 30, 2026)
The following rules were identified as "Shadow Logic" in the code and have been formally promoted to the evaluation pipeline:

| Rule | Threshold | Status | Justification |
| :--- | :--- | :--- | :--- |
| **Short Float Veto** | > 15% | Active, Under Evaluation | Prevents overcrowding; pending backtest on PEAD amplification. |
| **Piotroski F-Score** | ≤ 3 | **HARDENED** | Safety floor beneath S7 fundamental score. **PIT Requirement**: Fundamental queries MUST filter `fetched_at <= sim_date + 7` to eliminate look-ahead bias. |
| **Bear Drawdown** | > 65% | **PROMOTED** | Catastrophic loss prevention in structural breakdowns. |

### 10.2 Regime Handling & Decay Baseline Resets
Because signal weights are regime-dependent, a position carried through a macro regime transition (e.g., BEAR → HEALTHY) will experience an immediate artificial shift in its ensemble score as the weight vectors swap. To prevent this artifact from triggering a false `SCORE_DECAY_VETO`, the system monitors macro regime state changes daily. On the day a transition is detected, the decay veto is actively suppressed, and the position's `score_at_entry_baseline` is reset to the current score, providing a clean baseline for the new regime.

These rules are active in `e1_trader.py`. Any veto triggered by these rules must be logged in the EOD summary for Phase 2 evaluation.

## 11. Appendix: Post-Mortem Lessons (April 30, 2026)
- **Production/Backtest Divergence (Data-Gate Failure)**: Implementing auxiliary filters (Short Float, Piotroski) as INNER JOINs in production when the backtest did not use them caused the silent filtering of high-alpha candidates (e.g., GEV). Fix: Use LEFT JOINs for optional guards.
- **Non-Idempotent Reruns**: Rerunning the trader without checking Pending Orders led to the TSLA double-buy. Fix: Entry-guard must check for open orders.

## 12. Post-Market Stability Guards (Added May 2, 2026)
Following the May 1st recovery, the following "Robustness" guards are mandatory for the E1 Production Engine:

- **ID Integrity Rule**: The `e1_positions` table must maintain a sequential integer `PRIMARY KEY` (id). All database updates (Healing/Exits) must anchor to this ID (e.g., `WHERE id = 5`) to prevent syntax errors caused by `NULL` or `<NA>` identifiers during emergency liquidations.
- **Zero-Price Guard (Ghost Liquidation Prevention)**: The lifecycle engine must explicitly skip any ticker where the Alpaca quote returns `$0.00` or `None`. This prevents the engine from misinterpreting a post-market data gap or holiday close as a 100% stop-loss violation.
- **Validation-Error Veto**: The engine must never trigger an "Emergency Liquidation" based solely on an Alpaca API error code (e.g., `42210000`). Liquidation must only occur if a price violation is mathematically confirmed by the logic.
- **Recursive Stop Check (F-11)**: The reconciliation engine MUST check both top-level orders and nested legs (OCO/Bracket) when verifying protection state. Stop detection must be robust to Enum string prefixes (e.g., matching `ordertype.stop` vs `stop`).

---

## 13. Performance Attribution & Payoff Profile
(Validated May 2, 2026 via 2020 Stress Test)

The Strategy E1 V1.4 payoff profile is characterized by **Exit Category Asymmetry**, where specific triggers serve distinct roles in the equity curve:

| Exit Trigger | Role | Expected Payoff Profile |
| :--- | :--- | :--- |
| **Target 2 (T2)** | Alpha Engine | Large Wins (Avg +$540). Captures the core 4x ATR momentum. |
| **Time Exit (20D)** | Signal Baseline | Symmetric Noise (Avg +$180 / -$270). Realizes residual signal expectancy. |
| **Score Decay** | Loss Saver | Controlled Mitigation (Avg -$260). Exits when fundamental thesis dies. |
| **Stop Breach** | Risk Management | Rare Outliers (Avg -$900). Prevents catastrophic downside. |

### 13.1 Breakeven Progression Efficiency
The high win rate of the "Stop Breach" category (81.8% in 2020 validation) confirms the efficiency of the **Breakeven Progression** logic. Most stopped-out trades are exited at `Entry + 0.01`, successfully eliminating capital loss on positions that reach the +1.5x ATR trigger but fail to achieve Target 2.

---
**End of Specification V1.4**
