@echo off
rem Start the bot in this window (to see errors). Normally it starts by itself at Windows logon.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo Bot is running. Close this window to stop it. Log: data\tg-bot.log
py -3 bot.py
pause
