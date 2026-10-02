@echo off
rem One-time sign-in of Claude Code for the bot's AI help with resumes (uses your Claude subscription).
rem Double-click: a browser opens, sign in with the same account as in the Claude app.
setlocal
set "EXE="
for /d %%P in ("%LOCALAPPDATA%\Packages\Claude_*") do for /d %%V in ("%%P\LocalCache\Roaming\Claude\claude-code\*") do if exist "%%V\claude.exe" set "EXE=%%V\claude.exe"
if not defined EXE for /d %%V in ("%APPDATA%\Claude\claude-code\*") do if exist "%%V\claude.exe" set "EXE=%%V\claude.exe"
if not defined EXE (
  echo Claude Code not found. Open the Claude desktop app once, then run this again.
  pause
  exit /b 1
)
if /i "%~1"=="status" ( "%EXE%" auth status & exit /b )
echo Using: %EXE%
echo.
"%EXE%" auth login
echo.
"%EXE%" auth status
echo.
echo Done. The bot checks again within 5 minutes; then the AI buttons in the app light up.
pause
