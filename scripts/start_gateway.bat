@echo off
setlocal
cd /d "%~dp0.."
set "RF_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%RF_PYTHON%" (
    echo ERROR: .venv is missing. Create it and install gateway\requirements.txt first.
    pause
    exit /b 1
)
echo Starting RF Localization Gateway...
"%RF_PYTHON%" -m uvicorn main:app --app-dir "%~dp0..\gateway" --host 0.0.0.0 --port 8000
echo.
echo Gateway stopped.
pause
