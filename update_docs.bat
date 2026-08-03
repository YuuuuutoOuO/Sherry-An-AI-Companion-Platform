@echo off
chcp 65001 > nul
title DC Agent - update docs

cd /d "%~dp0"

python file\auto\scan_ast.py
if %errorlevel% neq 0 ( echo [ERROR] scan_ast.py failed & pause & exit /b 1 )

python file\auto\generate_docs.py
if %errorlevel% neq 0 ( echo [ERROR] generate_docs.py failed & pause & exit /b 1 )

echo.
echo Done. Docs/ updated.
echo.
pause
