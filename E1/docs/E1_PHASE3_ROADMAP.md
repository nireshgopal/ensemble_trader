# Strategy E1 Phase 3 Roadmap
**Status**: IN PROGRESS (CTE Simulation Training Started)

This document outlines the research and hardening tasks for Phase 3 of Strategy E1 "Flash".

---

## 1. Quantitative Research (Optimization)
- **Time Horizon Sensitivity**: Conduct 15, 25, and 30-day sensitivity testing on the current 20-day primary time exit. Determine if IC half-life has shifted in recent volatility regimes.
- **Regime-Specific Attribution**: Perform a deep-dive attribution on 2022 grinding bear FRAGILE-regime entries. Current signal mix for FRAGILE is based on 2020 (v-bottom) and needs validation for inflationary bear grinds.
- **S3 Sector Rank Integration**: Research the integration of Sector Rank (S3) into the scoring ensemble. Determine if IC weight should be diverted from S2 (3-Month RS) to S3.

## 2. Hardening & Bias Elimination
- **Sector RS Lookahead Removal**: Harden the Sector RS computation to strictly use `as_of_date - 1` Close prices. Current shadow mode uses same-day closes, introducing minor noise.
- **S7 IC Shrinkage Governance**: Implement automated monitoring of the rolling 60-day S7 (Fundamental) IC. Alert operator if IC drops below 0.035 for 5+ sessions.

## 3. Pillar 7: Overlays & Almanac
- **Shadow Mode Validation**: [IN PROGRESS] Executing multi-year (2014-2026) shadow run for CTE training (`cte_training_v1`).
- **Promotion Gate**: Compute Overlay IC from shadow data. Document the formal decision to either promote to live production or drop based on incremental Sharpe improvement.

---
*Derived from E1 Specification v1.4 and Legacy Enhancement Pillars.*
