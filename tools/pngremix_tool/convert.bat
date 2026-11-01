@echo off
setlocal
chcp 65001 > nul
title PNGTuber Remix 模型轉換工具

if "%~1"=="" (
    echo [使用方式] 請將 .pngRemix 檔案拖曳到此 .bat 上，或執行：
    echo convert.bat "路徑\你的模型.pngRemix"
    pause
    exit /b 1
)

python "%~dp0convert.py" "%~1" %*
pause
