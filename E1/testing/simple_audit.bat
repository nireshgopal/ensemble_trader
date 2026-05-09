@echo off
set LOG_DIR=C:\Users\nires\Side Gig\pixel-data-feeds\logs

echo ============================================================
echo STRATEGY E1: SEQUENTIAL ANNUAL AUDIT (2014-2026)
echo ============================================================

echo [STEP 0] Resetting Database...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --date 2014-01-01 --reset --run-id RESET_ONLY

echo [STEP 1] Running 2014...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2014-01-01 --end 2014-12-31 --run-id ANNUAL_AUDIT_2014 > "%LOG_DIR%\sim_audit_2014.log" 2>&1

echo [STEP 2] Running 2015...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2015-01-01 --end 2015-12-31 --run-id ANNUAL_AUDIT_2015 > "%LOG_DIR%\sim_audit_2015.log" 2>&1

echo [STEP 3] Running 2016...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2016-01-01 --end 2016-12-31 --run-id ANNUAL_AUDIT_2016 > "%LOG_DIR%\sim_audit_2016.log" 2>&1

echo [STEP 4] Running 2017...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2017-01-01 --end 2017-12-31 --run-id ANNUAL_AUDIT_2017 > "%LOG_DIR%\sim_audit_2017.log" 2>&1

echo [STEP 5] Running 2018...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2018-01-01 --end 2018-12-31 --run-id ANNUAL_AUDIT_2018 > "%LOG_DIR%\sim_audit_2018.log" 2>&1

echo [STEP 6] Running 2019...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2019-01-01 --end 2019-12-31 --run-id ANNUAL_AUDIT_2019 > "%LOG_DIR%\sim_audit_2019.log" 2>&1

echo [STEP 7] Running 2020...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2020-01-01 --end 2020-12-31 --run-id ANNUAL_AUDIT_2020 > "%LOG_DIR%\sim_audit_2020.log" 2>&1

echo [STEP 8] Running 2021...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2021-01-01 --end 2021-12-31 --run-id ANNUAL_AUDIT_2021 > "%LOG_DIR%\sim_audit_2021.log" 2>&1

echo [STEP 9] Running 2022...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2022-01-01 --end 2022-12-31 --run-id ANNUAL_AUDIT_2022 > "%LOG_DIR%\sim_audit_2022.log" 2>&1

echo [STEP 10] Running 2023...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2023-01-01 --end 2023-12-31 --run-id ANNUAL_AUDIT_2023 > "%LOG_DIR%\sim_audit_2023.log" 2>&1

echo [STEP 11] Running 2024...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2024-01-01 --end 2024-12-31 --run-id ANNUAL_AUDIT_2024 > "%LOG_DIR%\sim_audit_2024.log" 2>&1

echo [STEP 12] Running 2025...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2025-01-01 --end 2025-12-31 --run-id ANNUAL_AUDIT_2025 > "%LOG_DIR%\sim_audit_2025.log" 2>&1

echo [STEP 13] Running 2026...
.venv\Scripts\python.exe E1\testing\shadow_runner.py --start 2026-01-01 --end 2026-05-01 --run-id ANNUAL_AUDIT_2026 > "%LOG_DIR%\sim_audit_2026.log" 2>&1

echo ============================================================
echo AUDIT COMPLETE.
echo ============================================================
pause
