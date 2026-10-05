# Starts the old-bot stub («бот переехал → @PurrfectJob_bot») hidden at every logon, restarts it if it crashes.
# Run once:  powershell -ExecutionPolicy Bypass -File telegram\old_bot_autostart.ps1
# Remove:    Unregister-ScheduledTask -TaskName "purrfect old bot stub" -Confirm:$false

$pyw = & py -3 -c "import os, sys; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"
if (-not (Test-Path $pyw)) { throw "pythonw.exe not found: $pyw" }
$stub = Join-Path $PSScriptRoot "old_bot_stub.py"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$stub`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "purrfect old bot stub" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "purrfect old bot stub"
Write-Host "Old bot stub is running and will start automatically at every logon."
