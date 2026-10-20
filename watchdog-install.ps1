# Installs the bot watchdog: a scheduled task that runs telegram\watchdog.ps1 every 5 minutes, hidden.
# Install: powershell -ExecutionPolicy Bypass -File telegram\watchdog-install.ps1
# Remove:  powershell -ExecutionPolicy Bypass -File telegram\watchdog-install.ps1 -Remove
param([switch]$Remove)

$Name = "career-ops bot watchdog"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    Write-Host "Watchdog removed."
    exit 0
}

# wscript + vbs = no console window flashing every 5 minutes
$vbs = Join-Path $PSScriptRoot "watchdog.vbs"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Watchdog installed: checks the bot every 5 minutes (log: data\tg-watchdog.log)."
