@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

set "BOT_DIR=%~dp0"
if "%BOT_DIR:~-1%"=="\" set "BOT_DIR=%BOT_DIR:~0,-1%"
cd /d "%BOT_DIR%"
set "PYTHONPATH=%BOT_DIR%;%PYTHONPATH%"
set "SHERRY_TAB_RUNNER=1"

set PYTHON=C:\Users\corps\.conda\envs\discord_bot\python.exe
if not exist "%PYTHON%" set PYTHON=python

title Sherry Core

:main_loop
cls
echo.
echo ============================================================
echo [Sherry] Starting main.py ...
echo ============================================================

"%PYTHON%" main.py
set EXIT_CODE=%ERRORLEVEL%

set "SIGNAL_FILE=%BOT_DIR%\data\shutdown.signal"

if not exist "%SIGNAL_FILE%" goto no_signal

findstr /i "update" "%SIGNAL_FILE%" >nul 2>&1
if not errorlevel 1 (
    del "%SIGNAL_FILE%" >nul 2>&1
    echo.
    echo ============================================================
    echo [Sherry] Update signal received - running git pull ...
    echo ============================================================
    git pull origin dev
    echo [Sherry] Code updated. Waiting 2s before restart ...
    ping -n 3 127.0.0.1 > nul
    goto main_loop
)

findstr /i "restart" "%SIGNAL_FILE%" >nul 2>&1
if not errorlevel 1 (
    del "%SIGNAL_FILE%" >nul 2>&1
    echo.
    echo ============================================================
    echo [Sherry] Restart signal received. Waiting 2s ...
    echo ============================================================
    ping -n 3 127.0.0.1 > nul
    goto main_loop
)

findstr /i "stop" "%SIGNAL_FILE%" >nul 2>&1
if not errorlevel 1 (
    del "%SIGNAL_FILE%" >nul 2>&1
    echo.
    echo ============================================================
    echo [Sherry] Stop signal received. Shutting down.
    echo ============================================================
    goto finish
)

del "%SIGNAL_FILE%" >nul 2>&1

:no_signal
echo.
echo ============================================================
echo [Sherry] main.py exited (code: %EXIT_CODE%).
echo ============================================================

:finish
endlocal
