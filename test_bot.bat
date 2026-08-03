@echo off
cd /d "%~dp0"

echo ------------------------------------------------------------
echo Starting System Diagnostics and Testing...
echo ------------------------------------------------------------
echo.

if not exist ".venv\Scripts\python.exe" (
    echo WARNING: Virtual environment not found. Using global python...
    python -m unittest tests.test_core -v
) else (
    .venv\Scripts\python.exe -m unittest tests.test_core -v
)

echo.
pause
