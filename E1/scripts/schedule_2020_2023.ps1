# 2:00 AM Nightly Resume (2020-2023)
$targetTime = (Get-Date).Date.AddDays(1).AddHours(2)
$currentTime = Get-Date

Write-Host "Current Time: $currentTime"
Write-Host "Waiting until $targetTime to start 2020-2023 run..."

while ((Get-Date) -lt $targetTime) {
    Start-Sleep -Seconds 60
}

Write-Host "Target time reached. Starting 2020-2023 CTE Shadow Run..."

$years = 2020..2023
foreach ($year in $years) {
    Write-Host "[$year] Starting Shadow Run..."
    $logFile = "logs/shadow_$year.log"
    # Ensure UTF-8 and proper redirection
    uv run python E1/testing/shadow_runner.py --start "$year-01-01" --end "$year-12-31" --run-id "cte_training_v1" *>&1 | Out-File -FilePath $logFile -Encoding utf8
    Write-Host "[$year] COMPLETED."
}
