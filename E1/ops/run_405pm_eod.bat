@echo off
cd /d "C:\Users\nires\Side Gig\pixel-data-feeds"
if not exist logs mkdir logs

set T=%time: =0%
set LOGFILE=logs\eod_405pm_%date:~-4%-%date:~4,2%-%date:~7,2%_%T:~0,2%-%T:~3,2%-%T:~6,2%.log

echo ========================================================== >> %LOGFILE%
echo [%date% %time%] STARTING 4:05 PM EOD RECONCILIATION        >> %LOGFILE%
echo ========================================================== >> %LOGFILE%


echo Running Strategy E1 e1_reconciler.py... >> %LOGFILE%
uv run python refine\engine\e1_reconciler.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% NEQ 0 (
    uv run python scripts\notify_job.py "Strategy E1 Reconciliation" FAILED --detail "Closing the books failed"
)

echo. >> %LOGFILE%
echo Running IC Decay Monitor... >> %LOGFILE%
uv run python scripts\compute_live_ic.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% NEQ 0 (
    uv run python scripts\notify_job.py "IC Decay Monitor" FAILED --detail "Live IC detection crashed"
)

echo. >> %LOGFILE%
echo Updating IC History... >> %LOGFILE%
uv run python refine\pipeline\compute_signal_ic.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% NEQ 0 (
    uv run python scripts\notify_job.py "IC History Update" FAILED --detail "IC computation failed"
)

echo. >> %LOGFILE%
echo Triggering Weight Recompute (if needed)... >> %LOGFILE%
uv run python scripts\recompute_weights.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% NEQ 0 (
    uv run python scripts\notify_job.py "Weight Recompute" FAILED --detail "Automatic weight update failed"
)

echo. >> %LOGFILE%
echo Running Execution Fidelity Audit... >> %LOGFILE%
uv run python scripts/post_execution_audit.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% NEQ 0 (
    uv run python scripts\notify_job.py "Execution Audit" FAILED --detail "Fidelity breach detected in today's trades"
)

echo. >> %LOGFILE%
echo Refreshing SSOT Documentation... >> %LOGFILE%
uv run python scripts\update_docs.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% NEQ 0 (
    uv run python scripts\notify_job.py "Documentation Refresh" FAILED --detail "E1_SPECIFICATION.md update failed"
)

echo. >> %LOGFILE%
echo [%date% %time%] COMPLETED 4:05 PM EOD RECONCILIATION       >> %LOGFILE%
