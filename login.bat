@echo off
rem First run: log in to Telegram (phone + code) and preview what would be collected.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
py -3 telegram\tg_collect.py --dry-run
pause
