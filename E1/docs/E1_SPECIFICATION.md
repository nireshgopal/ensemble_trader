# Strategy E1: Unified Specification
**Single Source of Truth (SSOT)**

**Date**: May 10, 2026
**Status**: **PRODUCTION / LIVE**
**Version**: V1.5 (Pillar 7 Hardened)

## 1. Strategy Identity & DNA
**"Strategy E1 is a volatility-resilient breakout capture engine. It is structurally anti-fragile and utilizes a multi-layer ensemble to identify high-quality momentum. Phase 2 (Hardening) introduces the Pillar 7 CTE engine to dynamically scale risk based on the specific market context at entry (VIX velocity and Regime Age)."**

## 2. System Philosophy & Governance
Strategy E1 is a **Quality-First Ensemble** designed for 20-day alpha.

### Core Governance Rules
- **The 0.65 Gate**: No entry unless score >= 0.65 (Healthy) or 0.60 (Bear).
- **The 20-Day Horizon**: Mandatory time exit using `refined.price_history` as the authoritative calendar.
- **Circuit Breaker**: Regime-Gated ATR hard stop (6.0x/7.0x/8.0x).
- **Breakeven Progression**: Stop advances to entry (+0.01) at +1.5x ATR.
- **Decay Veto**: Exit if score drops 40% from baseline (reset on regime transition).
- **Almanac Vetoes**: Entry (5d window) and Exit (2d window) safety guards.
- **Gap-Up Veto**: No entry if live quote > 4.0% above prev close.
- **Pillar 7 (CTE)**: **(NEW)** Dynamic sizing multiplier (0.9x to 1.1x) based on VIX Momentum and Regime Age.

---

## 3. Risk & Sizing Architecture (S10 + CTE)
Final risk unit formula:
`risk_dollars = Base_Risk * Conviction_Scalar * S10_Macro_Scalar * CTE_Multiplier`

### 3.1 Continuous Conviction Scalar
- **Score 0.60**: 0.75x Scalar
- **Score 0.90**: 1.25x Scalar
- **Formula**: `0.75 + (score - 0.60) / 0.30 * 0.50` (Capped at 1.25x)

### 3.2 Dynamic Macro Scaling (S10)
- **Panic Recovery**: 1.25x
- **Credit Stress**: 0.50x
- **Credit Veto**: 0.00x (HY > 5.5)

### 3.3 Contextual Trade Estimator (CTE) - Pillar 7
CTE scales risk based on historical expectancy of the current "Cell":
- **VIX Momentum**: Quantile-binned 20-day VIX velocity (FALLING, STABLE, RISING).
- **Regime Age**: Days since last regime transition (FRESH, ESTABLISHED, MATURE).
- **Calibration**: Bayesian shrinkage (k=11.24) applied to the 12-year Audit Gold set.
- **Safety**: Hard cap of 1.50x on the combined scalar product (Conviction * CTE * S10).

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
- **Atomic Entry**: DB Insert -> Broker Submission -> Compensating Delete (on failure).
- **Zero-Price Guard**: Lifecycle engine skips $0.00 quotes to prevent data-gap liquidations.

---

## 6. Performance Benchmarks
| Benchmark | Sharpe | CAGR |
| :--- | :---: | :---: |
| **Audit Gold (2014-2026)** | 1.13 | 18.1% |
| **Live Gate (60 Session)** | **>= 0.85** | - |

**End of Specification V1.5**
