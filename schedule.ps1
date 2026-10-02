# Registers a Windows scheduled task that runs the collector at 09:00 and 20:00.
# If the computer was off at that time, it runs as soon as it is back on.
# Run once:  powershell -ExecutionPolicy Bypass -File telegram\schedule.ps1
# Remove:    Unregister-ScheduledTask -TaskName "career-ops telegram" -Confirm:$false

$bat = Join-Path $PSScriptRoot "run.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`""
$triggers = @(
    New-ScheduledTaskTrigger -Daily -At 09:00
    New-ScheduledTaskTrigger -Daily -At 20:00
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "career-ops telegram" -Action $action -Trigger $triggers -Settings $settings -Force | Out-Null
Write-Host "Scheduled: career-ops telegram, daily at 09:00 and 20:00."
