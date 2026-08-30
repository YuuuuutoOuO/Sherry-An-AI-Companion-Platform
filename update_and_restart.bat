@echo off
setlocal
chcp 65001 > nul
set BOT_DIR=%~dp0
set LOG_FILE=%BOT_DIR%\data\gitpull_result.txt
cd /d "%BOT_DIR%"

if not exist "%BOT_DIR%data" mkdir "%BOT_DIR%data"
echo update > "%BOT_DIR%data\shutdown.signal"

ping -n 3 127.0.0.1 > nul

echo [%DATE% %TIME%] Git Pull Started > "%LOG_FILE%"
git pull origin dev >> "%LOG_FILE%" 2>&1
echo [%DATE% %TIME%] Git Pull Finished >> "%LOG_FILE%"

call start_sherry_tabs.bat
endlocal
