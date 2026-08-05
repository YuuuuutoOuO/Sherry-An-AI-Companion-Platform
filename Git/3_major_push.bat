@echo off
chcp 65001 >nul
title Git Push - Major Release (v1.X.0)
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp03_major_push.ps1"
echo.
pause
