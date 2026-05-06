# 6:30 PM Tomorrow Resume (2024-2026)
# Calculates tomorrow at 18:30
$targetTime = (Get-Date).Date.AddDays(1).AddHours(18).AddMinutes(30)
$currentTime = Get-Date

Write-Host "Current Time: $currentTime"
Write-Host "Waiting until $targetTime to start 2024-2026 run..."

while ((Get-Date) -lt $targetTime) {
    Start-Sleep -Seconds 300
}

Write-Host "Target time reached. Starting 2024-2026 CTE Shadow Run..."

$years = 2024..2026
foreach ($year in $years) {
    Write-Host "[$year] Starting Shadow Run..."
    $logFile = "logs/shadow_$year.log"
    uv run python E1/testing/shadow_runner.py --start "$year-01-01" --end "$year-12-31" --run-id "cte_training_v1" *>&1 | Out-File -FilePath $logFile -Encoding utf8
    Write-Host "[$year] COMPLETED."
}
