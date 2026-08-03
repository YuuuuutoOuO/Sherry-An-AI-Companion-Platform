@echo off
:: ============================================================
:: Sherry Bot Update + Restart Launcher
:: 由 Web UI「Git Pull 更新並重啟」按鈕呼叫
:: 1. git pull origin dev  取得最新程式碼
:: 2. 啟動新的雪莉主程序
:: ============================================================
set PYTHON=C:\Users\corps\.conda\envs\discord_bot\python.exe
set BOT_DIR=%~dp0
set LOG_FILE=%BOT_DIR%\data\gitpull_result.txt

:: 等待舊進程關閉
timeout /t 2 /nobreak > nul

:: 切換到 Bot 目錄
cd /d "%BOT_DIR%"
if not exist "%BOT_DIR%\data" mkdir "%BOT_DIR%\data"

:: 執行 git pull 並將結果寫入 log 供 Web UI 查看
echo [%DATE% %TIME%] Git Pull Started > "%LOG_FILE%"
git pull origin dev >> "%LOG_FILE%" 2>&1
echo [%DATE% %TIME%] Git Pull Finished >> "%LOG_FILE%"

:: 啟動新的 Bot 進程（獨立視窗運行）
start "SherryBot" "%PYTHON%" main.py
