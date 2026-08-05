@echo off
chcp 65001 >nul
title Git Push - Fix Bug (v1.X.Z)
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp02_fix_push.ps1"
echo.
pause
