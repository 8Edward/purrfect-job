@echo off
rem One-time setup: Python packages, career-ops packages (job boards), config.yml.
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt || goto :error
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js not found - job boards will be off. Install it from https://nodejs.org and run setup.bat again.
) else (
  pushd ..
  call npm install || goto :error
  popd
)
if not exist config.yml (
  copy config.example.yml config.yml >nul
  echo Created telegram\config.yml - open it and fill in api_id, api_hash and bot_token.
) else (
  echo telegram\config.yml already exists.
)
py -3 test_tg_collect.py || goto :error
echo.
echo Setup OK. Next: fill config.yml, then run login.bat once to log in.
pause
exit /b 0
:error
echo Setup failed, see the error above.
pause
exit /b 1
