@echo off
chcp 65001 >nul
title Git Push - Dev Progress
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp01_dev_push.ps1"
echo.
pause
