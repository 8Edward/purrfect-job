@echo off
rem One-time setup: installs Python packages and creates config.yml.
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt || goto :error
if not exist config.yml (
  copy config.example.yml config.yml >nul
  echo Created telegram\config.yml - open it and fill in api_id and api_hash.
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
