# Watchdog for the bot: run every 5 minutes by the scheduled task "career-ops bot watchdog".
# Install / remove: powershell -ExecutionPolicy Bypass -File telegram\watchdog-install.ps1 [-Remove]
#
# Each run:
#   1. Once a day copies the owner's inbox/state to data\backups (only if they are valid JSON, keeps 14 days).
#   2. If a data file is broken (empty after a crash, cut JSON), puts back the newest good copy.
#   3. Restarts the bot when it is dead, the app port 8787 does not answer, or the log has been silent
#      for 45 minutes. Kills ONLY pythonw ... bot.py: never cloudflared (the app address would change for
#      everyone) and never old_bot_stub.py.
# Skips everything while there is no internet (a restart cannot help then) and leaves a freshly started
# bot alone for 15 minutes (a start can take ~7 min when Telegram is slow).

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$Data = Join-Path $Root "data"
$Backups = Join-Path $Data "backups"
$WdLog = Join-Path $Data "tg-watchdog.log"
$BotLog = Join-Path $Data "tg-bot.log"
$Task = "career-ops telegram bot"
$Python = "D:\tools\python312\python.exe"
$Files = @("tg-inbox.json", "tg-state.json")
$Grace = 15     # minutes after a bot start
$Silent = 45    # minutes without a log line = hung
$Cooldown = 10  # minutes between two restarts

function Say($text) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
    Add-Content -LiteralPath $WdLog -Value $line -Encoding UTF8
    # keep the log small
    if ((Get-Item -LiteralPath $WdLog).Length -gt 512KB) {
        $keep = Get-Content -LiteralPath $WdLog -Tail 2000 -Encoding UTF8
        Set-Content -LiteralPath $WdLog -Value $keep -Encoding UTF8
    }
}

function ValidJson($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    if ((Get-Item -LiteralPath $path).Length -lt 2) { return $false }
    & $Python -I -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" $path 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Online {
    foreach ($h in @("1.1.1.1", "8.8.8.8")) {
        $c = New-Object System.Net.Sockets.TcpClient
        try {
            if ($c.ConnectAsync($h, 443).Wait(3000) -and $c.Connected) { return $true }
        } catch {} finally { $c.Dispose() }
    }
    return $false
}

function AppAnswers {
    try {
        Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/" -TimeoutSec 20 | Out-Null
        return $true
    } catch [System.Net.WebException] {
        return ($null -ne $_.Exception.Response)   # 401/403/404 still means the server is alive
    } catch { return $false }
}

function BotProcs {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
        Where-Object { $_.CommandLine -match '\\telegram\\bot\.py' }
}

New-Item -ItemType Directory -Force -Path $Backups | Out-Null
$today = Get-Date -Format "yyyy-MM-dd"

# 1-2. backups and repair
$repaired = $false
foreach ($f in $Files) {
    $path = Join-Path $Data $f
    $base = [IO.Path]::GetFileNameWithoutExtension($f)
    if (ValidJson $path) {
        $copy = Join-Path $Backups "$base-$today.json"
        if (-not (Test-Path -LiteralPath $copy)) {
            Copy-Item -LiteralPath $path -Destination $copy -Force
            Say "backup: $f -> backups\$base-$today.json"
        }
    } elseif (Test-Path -LiteralPath $path) {
        $good = Get-ChildItem -LiteralPath $Backups -Filter "$base-*.json" | Sort-Object Name -Descending |
            Where-Object { ValidJson $_.FullName } | Select-Object -First 1
        if ($good) {
            Copy-Item -LiteralPath $path -Destination (Join-Path $Backups "$base-broken-$(Get-Date -Format yyyyMMdd-HHmmss).json") -Force
            Copy-Item -LiteralPath $good.FullName -Destination $path -Force
            Say "REPAIR: $f was broken, restored from backups\$($good.Name)"
            $repaired = $true
        } else {
            Say "REPAIR FAILED: $f is broken and there is no good backup"
        }
    }
}
Get-ChildItem -LiteralPath $Backups -Filter "*.json" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# 3. health
if (-not (Online)) { exit 0 }

$procs = @(BotProcs)
$reason = $null
if ($repaired) {
    $reason = "data file repaired"
} elseif ($procs.Count -eq 0) {
    $reason = "bot process not running"
} else {
    $started = ($procs | ForEach-Object { $_.CreationDate } | Sort-Object | Select-Object -First 1)
    if ((Get-Date) - $started -lt (New-TimeSpan -Minutes $Grace)) { exit 0 }
    if (-not (AppAnswers)) {
        $reason = "app port 8787 does not answer"
    } elseif ((Test-Path -LiteralPath $BotLog) -and
              ((Get-Date) - (Get-Item -LiteralPath $BotLog).LastWriteTime -gt (New-TimeSpan -Minutes $Silent))) {
        $reason = "log silent for $Silent+ min (hung)"
    }
}
if (-not $reason) { exit 0 }

$last = Get-Content -LiteralPath $WdLog -Tail 200 -Encoding UTF8 -ErrorAction SilentlyContinue |
    Where-Object { $_ -match ' RESTART ' } | Select-Object -Last 1
if ($last) {
    $when = [datetime]::ParseExact($last.Substring(0, 19), "yyyy-MM-dd HH:mm:ss", $null)
    if ((Get-Date) - $when -lt (New-TimeSpan -Minutes $Cooldown)) { exit 0 }
}

Say "RESTART ($reason)"
Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
foreach ($p in @(BotProcs)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3
Start-ScheduledTask -TaskName $Task
Start-Sleep -Seconds 20
$now = @(BotProcs)
if ($now.Count -gt 0) { Say "bot started, pid $($now[0].ProcessId)" } else { Say "bot did NOT start: see data\tg-bot.log" }
