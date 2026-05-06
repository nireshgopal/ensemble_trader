Rule #1: Be honest. It is always okay to say "I don't know" or admit a mistake. Accuracy and integrity take precedence over speed.

Do not overcomplicate the code. Keep it simple and efficient.
Never ever over-do. Do not take action or execute fixes unless explicitly asked to do so. Proposing a plan is fine; executing it requires a clear "Go".

Its okay to say "I don't know" or "I need more information".

You always dont have to give me an answer. there are lot of things that you dont know and thats okay.

8: **Communication Discipline**: Always answer the user's specific question first. Clarifications and forensic details are good, but never jump into an implementation plan or a solution proposal until the data requested has been presented and understood.
9: 
10: **Concurrency & Schedule Awareness**: This repository operates on a scheduled heartbeat (see `scripts\setup_tasks.ps1`). Before executing any long-running command or database-locking operation, the AI MUST check the automated schedule for potential collisions and warn the user.
11: 
12: **Holistic Auditing**: "Checking logs" or verifying "all is well" implies a system-wide audit of all log files in the `logs/` directory for the relevant time window. Verification must never be limited to the success of the current task.
13: 
14: .bat scripts should go in /scripts folder. not in the folder where the python scripts are.
15: 
16: # Strategy E1 Engineering Rules (Mandatory)

> [!IMPORTANT]
> **BOOTSTRAP INSTRUCTION**: All AI Agents must read and acknowledge these rules before proposing or executing any database schema or pipeline architecture changes.
> **STRICT EXECUTION BOUNDARY**: Proposing a "how to fix" does NOT constitute permission to execute. Execute only on a direct, explicit command to "Go" or "Perform the fix".

## 1. Database Safety (Zero-Loss Policy)
- **NO DROPS**: Never execute `DROP TABLE` on any production or historical table (e.g., `yahoo.analyst_data`, `yahoo.yahoo_raw`).
- **SCHEMA CHANGES**: Use `ALTER TABLE ADD COLUMN` for all expansions. 
- **LIMIT 0**: Never pass `limit=0` unless the underlying code explicitly handles it as "Stop" (not "Unlimited").

## 2. Data Governance ("The Strict Way")
- **RAW-THEN-LOAD**: Every data-ingestion pipeline must save the original JSON/API blob into a `_raw` table (e.g., `yahoo.yahoo_raw`) before extracting attributes.
- **HISTORY PRESERVATION**: If a new column is added, it must be back-filled from the `_raw` history blobs before the first production run.
- **AUDIT BEFORE ASSUME**: Before declaring a data "blind spot," run a `SELECT COUNT(*)` on the existing tables.

## 3. Workspace Organization
- **SCRATCH CODE**: All diagnostic, audit, test, and research scripts MUST be created in the `scratch/` folder (e.g., `scratch/audit_xyz.py`).
- **SCRIPTS FOLDER**: The `scripts/` directory is reserved for **permanent, production** scripts and automation (.bat/.ps1) only. No temporary or one-off files.
- **FOLDER INTEGRITY**: Never leave temporary `.py` files in production directories (`refine/`, `yahoo/`, `schwab/`, `scripts/`, etc.).
- **EPHEMERALITY**: Files in the `scratch/` folder are considered temporary and should not be tracked unless specifically needed for production.

## 4. Universe Definition
- **THE 929 UNIVERSE**: Strategy E1 operates on roughly **929 tickers**. This includes:
    - **S&P 500** (Large Cap)
    - **S&P 400** (Mid Cap)
    - **Core ETFs**
- Source of truth for membership is **`schwab.watchlists`**, not just `refined.tickers` flags.

## 5. Strategy E1 "Hardened" Vetoes
- **UNIVERSAL VETO**: Any ticker with **Short Float > 15%** is a hard 0.0 Conviction score.
- **ETF EXEMPTION**: Index/ETF products are exempt from the Short Float veto.
- **RATIONALE**: Every veto must be logged in the `rationale` column as `VETO: [Condition] ([Value])`.

## 6. Python Environment
- **MANDATORY**: All script invocations must use `.venv\Scripts\python.exe` (or `uv run python`).
- System Python (duckdb 0.9.2) cannot read `findb.duckdb`. Treat system Python invocation as a **hard error**, not a warning.
- Production stop multiplier baseline: **2.0× ATR flat** (rollback target if V1.3 cluster-specific underperforms).

## 7. Execution Governance
- **CORE ONLY**: Any trade execution (orders, cancellations, bracket updates) MUST happen exclusively via the modules located in `E1/core/` (e.g., `e1_trader.py`, `e1_reconciler.py`). 
- **NO BYPASS**: Never use legacy scripts (e.g., `scripts/alpaca_executor.py`) or external tools to interact with the Alpaca API for E1 positions.
