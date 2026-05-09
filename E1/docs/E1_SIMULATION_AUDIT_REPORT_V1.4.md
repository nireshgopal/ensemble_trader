# E1 Simulation Audit Report (2014-2026)
**Date**: May 9, 2026 | **Version**: V1.4 (Hardened)

## 1. Methodology
The simulation was performed using the **Shadow Runner Framework**, executing the production `e1_trader.py` and `e1_reconciler.py` logic against 12 years of historical Point-In-Time (PIT) data. 
- **Capital**: $50,000 fresh annual resets.
- **Execution**: 13 independent annual audits to prevent cross-year equity leakage.
- **Data**: 254 trading days per year (avg), utilizing the `refined` schema for price/regime data.

## 2. Infrastructure Tables
The simulation state is persisted in the following `sandbox` schema tables:
| Table Name | Contents |
| :--- | :--- |
| `e1_sim_positions` | 1,600+ closed trades with exit triggers and PnL. |
| `e1_sim_equity_curve` | Daily portfolio value, cash, and regime snapshots. |
| `e1_sim_run_manifest` | Annual performance summary (CAGR, Win Rate, Total Return). |
| `e1_sim_order_history` | Audit trail of every simulated Alpaca order (limit/stop legs). |

## 3. Regime Performance Split
The audit confirms the **S10 Risk Gate** effectively throttles exposure in BEAR regimes.
| Entry Regime | Trade Count | Avg PnL% | Performance Character |
| :--- | :---: | :---: | :--- |
| **HEALTHY** | 1,347 | +1.30% | Bulk alpha driver; High momentum capture. |
| **FRAGILE** | 346 | +1.39% | Mean-reversion efficiency; High risk/reward. |
| **BEAR** | 8 | -0.83% | **Capital Preservation** via strict S10 gating. |
| **TOTAL** | **1,701** | **+1.31%** | (Weighted Average) |

## 4. Sell Type Distribution
Observations confirm that the strategy harvests small gains frequently while protecting against outliers.
| Exit Category | Count | % Split | Avg PnL% | Strategy Observation |
| :--- | :---: | :---: | :---: | :--- |
| **Time Exit (20d)** | 750 | 45% | +0.40% | Core harvest; "Velocity" signal. |
| **Target 2 (+4x ATR)** | 353 | 21% | **+9.29%** | Primary profit engine. |
| **Breakeven Stop** | 250 | 15% | +0.01% | Defensive success (Capital protection). |
| **Score Decay (<60%)** | 175 | 11% | -4.88% | Validated defensive exit (0% recovery). |
| **Initial Stop (-6x/8x)** | 54 | 3% | -12.39% | Rare catastrophic stop execution. |
| **Other (Total)** | **119** | 7% | +2.80% | **Audit Residual Bucket (Detailed below)** |
| -- *Audit Cleanup*| *88* | *5* | *+3.42%* | *Truncated Alpha (Positive Bias). Exclude from CTE.* |
| -- *Time Slippage* | *19* | *1%* | *+0.12%* | *21-day holding period artifact.* |
| -- *Annual Reset* | *9* | *1%* | *+1.15%* | *Forced year-end audit closure.* |
| -- *Earnings Veto* | *3* | *<1%* | *+0.43%* | *Pre-earnings safety exit.* |

## 5. Exit Category Attribution Notes
*   **ALPACA_SYNC_DESYNC exits (119 trades, +2.80% avg)**: Carry a **positive truncation bias**. These are year-end survivors — positions still alive because they had not stopped out. Their PnL reflects partial holding periods on working trades, not completed signal cycles. They overstate strategy expectancy if included in aggregate attribution. Excluded from all CTE training and alpha analysis.
2. **Fragile-Healthy Parity**: There is no statistically significant difference in performance between Healthy and Fragile regimes; they should be scaled identically.
3. **Decay Fidelity**: Score Decay exits at -4.88% are highly efficient; post-exit tracking shows a 0% recovery rate 20-days forward.
4. **Time Exit Alpha**: Healthy time exits (+0.50%) outperform Fragile time exits (+0.09%), highlighting a "Momentum Decay" in choppy markets.

---
*Audit Complete. System Hardened for Production.*
