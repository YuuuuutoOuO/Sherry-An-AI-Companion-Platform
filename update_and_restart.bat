@echo off
setlocal
chcp 65001 > nul
set "BOT_DIR=%~dp0"
if "%BOT_DIR:~-1%"=="\" set "BOT_DIR=%BOT_DIR:~0,-1%"
set LOG_FILE=%BOT_DIR%\data\gitpull_result.txt
cd /d "%BOT_DIR%"

if not exist "%BOT_DIR%\data" mkdir "%BOT_DIR%\data"
echo update > "%BOT_DIR%\data\shutdown.signal"

set PYTHON=C:\Users\corps\.conda\envs\discord_bot\python.exe
if not exist "%PYTHON%" set PYTHON=python

"%PYTHON%" -c "from tools.common import get_main_bot_pid, is_pid_running; pid = get_main_bot_pid(); exit(0 if pid and is_pid_running(pid) else 1)" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [雪莉更新] 偵測到雪莉主核心正在運行，已發送更新重啟信號（主核心將自動執行 Git Pull 並在原分頁重啟）。
    endlocal
    exit /b 0
)

echo [%DATE% %TIME%] Git Pull Started > "%LOG_FILE%"
git pull origin dev >> "%LOG_FILE%" 2>&1
echo [%DATE% %TIME%] Git Pull Finished >> "%LOG_FILE%"

echo [雪莉更新] 雪莉未在運行，代碼更新完成，正在啟動多分頁工作區...
call start_sherry_tabs.bat
endlocal
