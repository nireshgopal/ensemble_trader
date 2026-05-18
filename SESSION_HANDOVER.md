# Strategy E1: AI Governance & Handover

## PHASE 0: MANDATORY ENVIRONMENT ONBOARDING
**To ensure full situational awareness, the AI Agent MUST execute these steps before starting any task:**

1. **Governance Check**: Read `rules.md` and acknowledge **Rule #1: Be Honest**.
2. **Architecture Review**: Perform a recursive `ls` of the `E1/` folder to understand the module structure.
3. **Master Specification**: Read `E1/docs/E1_SPECIFICATION.md`.
4. **Database Audit**:
   - **Path**: `C:\Users\nires\Side Gig\pixel-data-feeds\data\findb.duckdb`
   - **Action**: Explore schemas `refined`, `yahoo`, and `sandbox` (especially `e1_sim_*` and `e1_cte_lookup`).
5. **Log Review**: Scan the latest files in `C:\Users\nires\Side Gig\pixel-data-feeds\logs\` for recent execution status.

---

## PHASE 1: CURRENT SESSION HANDOVER
**Last Update**: 2026-05-18 (V1.7.1 Decay Veto Reform & Return Improvement Integration)

### 1. Final Phase 5 Forensic Audit & V1.7.1 Return Improvement Results
The system has officially frozen **Fix 1: Decay Veto Reform** as its new production baseline (`V1.7.1_DECAY`).
Forensic testing across defensive and offensive segments proved the context-aware decay veto significantly improves trade-exit efficiency and risk-adjusted returns:

*   **Segment 2 (Mixed: 2019-2022)**:
    *   **Return**: **$+27.28\%$** (vs Baseline $+23.06\%$, a **$+422$ bps** outperformance! 🚀)
    *   **Max Drawdown**: **$-17.96\%$** (vs Baseline $-18.90\%$, a **$+94$ bps** risk reduction! 🛡️)
    *   **Win Rate**: Unchanged at $64.0\%$ over $331$ trades.
*   **Segment 3 (Bull: 2023-2024)**:
    *   **Return**: **$+64.90\%$** (vs Baseline $+64.93\%$, noise-scale identical)
    *   **Max Drawdown**: **$-6.34\%$** (vs Baseline $-6.38\%$)
    *   **Trade Quality**: Captured **+2 extra full Target 2 hits** (64 vs 62). Slashed average decay-veto loss by more than half (**$-1.66\%$** vs **$-3.86\%$**), successfully preventing premature chopping in bull consolidations.

### 2. Status & Wins (Phase 5 Complete)
- **Weight Promotion**: The Phase 5 candidate weights (`signal_weights_candidate.json`) were promoted to production (`docs/signal_weights.json`) and officially tagged as `E1_V1.7_WEIGHTS`.
- **Decay Veto Reform Integrated**: Dynamic regime-age-aware score decay veto (35% to 55% threshold) with a +5% "breathing room" release successfully implemented in `exit_evaluator.py` and promoted to main (`V1.7.1_DECAY`).
- **MockAlpacaClient Death Spiral Fixed**: Moved the `MockAlpacaClient` instantiation outside of the simulation day-loop and added dynamic `sim_date` setters. This eliminated the daily state-reset that was causing massive `ALPACA_SYNC_DESYNC` ghost trades.
- **Regime Metadata Fix**: Patched `e1_trader.py` SQL mapping to correctly write `regime_at_entry`, eliminating NULLs in `sandbox.e1_sim_positions` and allowing accurate entry-regime grouping.

### 3. CTE Stage-1 Seeding & Calibration Complete
The Contextual Training Engine (CTE) has been successfully calibrated and seeded on the complete V1.6/V1.7 architecture for the first time.
*   **Seeding Table (`sandbox.e1_cte_lookup`)**: Programmatically populated with all 27 rows covering the $3 \times 3 \times 3$ space, ensuring no silent nulls at runtime.
*   **Stage-1 Sizing corridor**: Sized at `0.90x – 1.10x` narrow corridor (shaded linearly based on cell Sharpe).
*   **Status**: **CTE Stage-1 Shadow Mode Active** (`cte_mult_active = False` in `config.py`). The system is logging theoretical CTE sizes at every entry for parallel analysis.

### 4. Next Steps & Activation Path
1.  **Technical Implementation of Fix 4 (35d Hold Extension Window)**:
    *   **Goal**: Replace the rigid Day 20 time-exit with an evaluation window (Days 19–21) in `E1/core/exit_evaluator.py` and apply a ratcheting 2.5x to 2.0x ATR trail to capture right-tail bull momentum.
    *   **Sim Validation**: Run Segment 3 (Offense) and Segment 2 (Defense) backtests with CTE inactive to isolate performance.
2.  **CTE Verification (Live Only)**:
    *   Keep CTE in shadow mode for simulations. After ~30 live sessions, analyze logged sizes and flip `cte_mult_active = True` on live-only if results reconcile.
3.  **Full 2014-2025 Audit Rerun**:
    *   Once Fix 1 and Fix 4 are both fully integrated, execute a complete 12-year forensic backtest to establish the new unified baseline (`V1.8_AUDIT`).
 
