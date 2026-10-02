# Starts the bot automatically (hidden, no window) every time you log in to Windows,
# and restarts it if it crashes.
# Run once:  powershell -ExecutionPolicy Bypass -File telegram\autostart.ps1
# Remove:    Unregister-ScheduledTask -TaskName "career-ops telegram bot" -Confirm:$false

# Pin the exact interpreter `py -3` picks now: pyw.exe inside a scheduled task can
# resolve to a different (older) Python than the one in an interactive shell.
$pyw = & py -3 -c "import os, sys; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"
if (-not (Test-Path $pyw)) { throw "pythonw.exe not found: $pyw" }
$bot = Join-Path $PSScriptRoot "bot.py"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$bot`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "career-ops telegram bot" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "career-ops telegram bot"
Write-Host "Bot is running and will start automatically at every logon."
