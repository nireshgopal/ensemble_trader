@echo off
set RUN_ID=cte_training_v1
set START_YEAR=2014
set END_YEAR=2026

echo Starting Strategy E1 CTE Training Run: %START_YEAR% to %END_YEAR%
echo Run ID: %RUN_ID%
echo.

:: Loop through years
for /L %%Y in (%START_YEAR%, 1, %END_YEAR%) do (
    echo [%%Y] Starting Shadow Run...
    set ARGS=--start %%Y-01-01 --end %%Y-12-31 --run-id %RUN_ID%
    
    if "%%Y"=="%START_YEAR%" (
        echo [%%Y] First year: Enabling --reset
        uv run python E1/testing/shadow_runner.py --start %%Y-01-01 --end %%Y-12-31 --run-id %RUN_ID% --reset > logs/shadow_%%Y.log 2>&1
    ) else (
        uv run python E1/testing/shadow_runner.py --start %%Y-01-01 --end %%Y-12-31 --run-id %RUN_ID% > logs/shadow_%%Y.log 2>&1
    )
    
    if errorlevel 1 (
        echo [%%Y] ERROR: Run failed. Check logs/shadow_%%Y.log
        exit /b 1
    )
    echo [%%Y] COMPLETED.
    echo.
    timeout /t 10 /nobreak > nul
)

echo.
echo All years completed successfully.
