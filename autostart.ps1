# Starts the bot automatically (hidden, no window) every time you log in to Windows,
# and restarts it if it crashes.
# Run once:  powershell -ExecutionPolicy Bypass -File telegram\autostart.ps1
# Remove:    Unregister-ScheduledTask -TaskName "career-ops telegram bot" -Confirm:$false

$pyw = (Get-Command pyw.exe -ErrorAction Stop).Source
$bot = Join-Path $PSScriptRoot "bot.py"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "-3 `"$bot`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "career-ops telegram bot" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "career-ops telegram bot"
Write-Host "Bot is running and will start automatically at every logon."
