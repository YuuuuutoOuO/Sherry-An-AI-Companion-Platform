@echo off
setlocal
chcp 65001 > nul
set BOT_DIR=%~dp0
cd /d "%BOT_DIR%"

if not exist "%BOT_DIR%data" mkdir "%BOT_DIR%data"
echo restart > "%BOT_DIR%data\shutdown.signal"

ping -n 3 127.0.0.1 > nul

call start_sherry_tabs.bat
endlocal
