@echo off
rem Collect job posts from Telegram into data\pipeline.md. Log: data\tg-collect.log
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
py -3 telegram\tg_collect.py %* >> data\tg-collect.log 2>&1
