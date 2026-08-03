@echo off
:: ============================================================
:: Sherry Bot Restart Launcher
:: 由 Web UI「重啟雪莉」按鈕呼叫
:: 確保在 conda 環境下乾淨地重新啟動 main.py
:: ============================================================
set PYTHON=C:\Users\corps\.conda\envs\discord_bot\python.exe
set BOT_DIR=%~dp0

:: 等待舊進程關閉
timeout /t 2 /nobreak > nul

:: 切換到 Bot 目錄並啟動
cd /d "%BOT_DIR%"
start "SherryBot" "%PYTHON%" main.py
