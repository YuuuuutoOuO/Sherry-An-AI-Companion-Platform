@echo off
setlocal
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

set "BOT_DIR=%~dp0"
if "%BOT_DIR:~-1%"=="\" set "BOT_DIR=%BOT_DIR:~0,-1%"
cd /d "%BOT_DIR%"
set "PYTHONPATH=%BOT_DIR%;%PYTHONPATH%"


set PYTHON=C:\Users\corps\.conda\envs\discord_bot\python.exe
if not exist "%PYTHON%" set PYTHON=python

where wt.exe >nul 2>nul
if errorlevel 1 goto fallback

start "" wt.exe -d "%BOT_DIR%" --title "Sherry Core" --tabColor "#FF69B4" "%PYTHON%" main.py ; new-tab -d "%BOT_DIR%" --title "Web UI Monitor" --tabColor "#00BFFF" "%PYTHON%" tools\monitor_web.py ; new-tab -d "%BOT_DIR%" --title "Brain Monitor" --tabColor "#9370DB" "%PYTHON%" tools\monitor_brain.py ; new-tab -d "%BOT_DIR%" --title "Error Sentry" --tabColor "#FF4500" "%PYTHON%" tools\monitor_sentry.py
goto end

:fallback
start "Sherry Core" "%PYTHON%" main.py
start "Web UI Monitor" "%PYTHON%" tools\monitor_web.py
start "Brain Monitor" "%PYTHON%" tools\monitor_brain.py
start "Error Sentry" "%PYTHON%" tools\monitor_sentry.py

:end
endlocal
