@echo off
setlocal
chcp 65001 > nul
title 🌐 雪莉 Web 控制台快速啟動器

set "TARGET_URL=https://26.108.83.196:8763"

:: 支援參數指定：stream / 8764 則開啟直播大螢幕
if /i "%~1"=="stream" set "TARGET_URL=https://26.108.83.196:8764"
if /i "%~1"=="8764"   set "TARGET_URL=https://26.108.83.196:8764"
if /i "%~1"=="live"   set "TARGET_URL=https://26.108.83.196:8764"

echo 正在開啟雪莉 Web 控制台 (%TARGET_URL%)...

:: 尋找常用瀏覽器以 App 模式獨立啟動
set "BROWSER_BIN="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    set "BROWSER_BIN=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
    set "BROWSER_BIN=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
) else if exist "%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe" (
    set "BROWSER_BIN=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"
) else if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_BIN=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_BIN=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set "BROWSER_BIN=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)

if defined BROWSER_BIN (
    start "" "%BROWSER_BIN%" --app=%TARGET_URL%
) else (
    start "" "%TARGET_URL%"
)

endlocal
