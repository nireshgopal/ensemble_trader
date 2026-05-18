# Strategy E1: Unified Specification
**Single Source of Truth (SSOT)**

**Date**: May 16, 2026
**Status**: **PRODUCTION / LIVE**
**Version**: V1.7.1 (Decay Veto Reform)

## 1. Strategy Identity & DNA
**"Strategy E1 is a volatility-resilient breakout capture engine. It is structurally anti-fragile and utilizes a multi-layer ensemble to identify high-quality momentum. Phase 2 (Hardening) introduces the Pillar 7 CTE engine to dynamically scale risk based on the specific market context at entry (VIX velocity and Regime Age)."**

## 2. System Philosophy & Governance
Strategy E1 is a **Quality-First Ensemble** designed for 20-day alpha.

### Core Governance Rules
- **Regime-Aware Gates**: No entry unless score >= **0.72 (Healthy)**, **0.68 (Fragile)**, or **0.80 (Bear)**.
- **Daily Entry Caps**: Max **2 new entries** in Healthy regimes; **1 new entry** in others.
- **The 20-Day Horizon**: Mandatory time exit using `refined.price_history` as the authoritative calendar.
- **Circuit Breaker**: Regime-Gated ATR hard stop (6.0x/7.0x/8.0x).
- **Breakeven Progression**: Stop advances to entry (+0.01) at +1.5x ATR.
- **Decay Veto**: Dynamic, regime-age-aware score decay veto (35% to 55% threshold) with a +5% "breathing room" release if core technical signals remain intact.
- **Almanac Vetoes**: Entry (5d window) and Exit (2d window) safety guards.
- **Gap-Up Veto**: No entry if live quote > 4.0% above prev close.
- **Exhaustion Penalty**: Integrated RSI and Drawdown Recovery weights to penalize overextended setups.
- **Pillar 7 (CTE)**: **(AUDIT MODE)** Theoretical sizing multiplier (0.9x to 1.1x) logged but currently inactive (1.0x) for the V1.5 Gold Run.

---

## 3. Risk & Sizing Architecture (S10 + CTE)
Final risk unit formula:
`risk_dollars = Base_Risk * Conviction_Scalar * S10_Macro_Scalar * CTE_Multiplier`

### 3.1 Continuous Conviction Scalar (Offensive Lever)
- **Score 0.68**: 1.00x Scalar (Baseline)
- **Score 0.80**: 1.35x Scalar
- **Score 0.90+**: 1.50x Scalar
- **Formula**: Linear interpolation between tiers. 

### 3.2 Regime-Based Risk Units
- **HEALTHY**: 1.50% risk per trade.
- **FRAGILE**: 0.50% risk per trade.
- **BEAR**: 0.25% risk per trade.

### 3.3 Contextual Trade Estimator (CTE) - Pillar 7
CTE scales risk based on historical expectancy of the current "Cell":
- **VIX Momentum**: Quantile-binned 20-day VIX velocity (FALLING, STABLE, RISING).
- **Regime Age**: Days since last regime transition (FRESH, ESTABLISHED, MATURE).
- **Status**: Currently **DISABLED** (1.0x) for the 13-year audit to establish a raw baseline.
- **Safety**: Hard cap of 1.75x on the combined scalar product (Conviction * CTE * S10).

### 3.4 Context-Aware Decay Veto
The Decay Veto protects the portfolio from structural signal roll-over. Rather than applying a flat 40% drop threshold, it utilizes a context-aware lookup based on current market regime and regime age:

| Market Regime | Fresh (<30d) | Established (30d–89d) | Mature (>=90d) |
| :--- | :---: | :---: | :---: |
| **HEALTHY** | 55% threshold | 42% threshold | 35% threshold |
| **FRAGILE** | 40% threshold | 35% threshold | 28% threshold |
| **BEAR** | 38% threshold | 30% threshold | 25% threshold |

* **Core Technical Release (Breathing Room)**: If core signals are intact (Minervini Stage 2 is confirmed and 12-month relative strength is positive), the exit threshold is widened by **+5%** to allow fresh breakouts to consolidate.

---

## 4. Exit Hierarchy & Attribution
To maintain a clean CTE training set, exits are categorized by **Structured Triggers**:

| Exit Trigger | Type | Priority | CTE Impact |
| :--- | :--- | :---: | :--- |
| **STOP_VIOLATION** | Risk | 1 | Included |
| **TARGET_2_HIT** | Alpha | 1 | Included |
| **TIME_EXIT_20D** | Horizon | 2 | Included |
| **ALMANAC_EXIT_VETO** | Safety | 3 | Excluded |
| **SCORE_DECAY_VETO** | Signal | 3 | Included |
| **ALPACA_SYNC_DESYNC** | Maintenance| 0 | **EXCLUDED (Bias Guard)** |

---

## 5. Shadow Rule Governance & Data Integrity
- **Piotroski PIT Rule**: All fundamental queries MUST filter `fetched_at <= sim_date` (Strict window). Look-ahead bias (+7 day) is prohibited.
- **Low Confidence Veto**: Piotroski scores derived from mismatched sources (TTM CFO vs Quarterly NI) or thin data (< 7/9 points) are marked `LOW_CONFIDENCE` and treated as non-authoritative for hard vetoes.
- **Abstain-on-Stale (Fix A)**: S7 (PEAD Fundamental) signal must strictly abstain (`None`) if data is missing or stale (> 60 days). The ensemble scorer must exclude abstaining signals from the active-weight denominator to prevent artificial score suppression in growth names between earnings prints.
- **Atomic Entry**: DB Insert -> Broker Submission -> Compensating Delete (on failure).
- **Zero-Price Guard**: Lifecycle engine skips $0.00 quotes to prevent data-gap liquidations.

---

## 6. Performance Benchmarks
| Benchmark | Sharpe | CAGR |
| :--- | :---: | :---: |
| **Audit Gold (2014-2026)** | 1.13 | 18.1% |
| **Phase 5 Final Audit (2014-2025)** | **N/A** | **9.17% (Max DD: -13.44%)** |
| **Live Gate (60 Session)** | **>= 0.85** | - |

---

## 7. Strategy E1 V1.6: Healthy Bull Hold Extension (Offensive Gear)
To mitigate structural lag in strong bull markets, the 20-day time exit is conditionally uncapped to **35 days** if the specific "Healthy Bull" continuation thesis remains intact.

### 7.1 Extension Eligibility (The Day 20 Gate)
On trading day 20, a position is eligible for a **15-day extension** if and only if:
- **Condition 1: Market Regime**: Current regime is **HEALTHY**.
- **Condition 2: Trend Confirmation**: SPY close is above both 50-day and 200-day SMA.
- **Condition 3: Volatility Environment**: VIX (Prior Day Close) is **≤ 18.0**.
- **Condition 4: Current Conviction**: Current ensemble score remains **≥ 0.72** (consistent with HEALTHY entry threshold).
- **Condition 5: Conviction at Entry**: Trade was a "High Conviction" entry (Score **≥ 0.80**) to ensure quality foundation.
- **Condition 6: Profitability Gate**: Trade must be at or above **T1 Profit (+2.0 ATR)** to prevent extending losers.

> [!IMPORTANT]
> **Metadata Requirements**: The Healthy Bull gate requires the following keys in `mdata_dict`: `vix_current`, `spy_price`, `spy_sma50`, `spy_sma200`. These must be mapped from `refined.market_regime` (vix_close, spy_close, etc.) by the trader engine. Missing keys must trigger a HARD FAIL in simulation to prevent silent logic gaps.

### 7.2 Extended State Governance
- **New Exit Trigger**: `TIME_EXIT_EXT` (Day 35 Hard Cap).
- **Extension Stop-Loss**: Trailing multiplier is widened to **2.5x ATR** (Ratchet-Only) to provide "breathing room" for the momentum run while protecting the majority of the profit.
- **Defensive Precedence**: All defensive exits (`REGIME_EXIT`, `ALMANAC_EXIT`, `SCORE_DECAY`) take precedence and will terminate the extension immediately if triggered.

## 8. Strategy E1 Phase 5: Technical Signal Expansion
The Phase 5 expansion (implemented May 2026) hardens the technical ensemble by integrating five additional momentum and confirmation signals.

### 8.1 Signal Inventory (The "New 5")
- **S_A (sig_rs_12month)**: 12-month Relative Strength vs SPY (Skip-1-Month).
- **S_B (sig_rs_6month)**: 6-month Relative Strength vs SPY (Skip-1-Month).
- **S_C (sig_price_stage)**: Minervini-style Trend Structure (Close > 50 > 150 > 200).
- **S_D (sig_52w_high)**: Proximity to 52-week High (using high_252d/low_252d range).
- **S_E (sig_volume)**: Volume Confirmation (21d avg volume vs 63d avg volume).

### 8.2 Data Requirements & Abstention Rules
- **Lookback Integrity**: `sig_rs_12month` requires strictly **252 days** of price history. Tickers with fewer than 252 days MUST abstain (`None`). No interpolation or partial lookbacks are permitted.
- **Expected Gaps**: A 1–2% coverage gap in `sig_rs_12month` (relative to `sig_rs_6month`) is expected and identifies recent IPOs or index additions. This is a design feature to ensure momentum is measured only against mature price structures.
- **Status**: **ACTIVE (V1.7)**. The Phase 5 signals have successfully cleared the 12-year Forensic Audit. Production weights have been updated and are strictly enforced via `WEIGHTS_MODE = "frozen"`.

**End of Specification V1.7**

