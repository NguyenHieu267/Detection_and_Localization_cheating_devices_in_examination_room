@echo off
setlocal
set "RF_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%RF_PYTHON%" (
    echo ERROR: Create .venv in the project root and install requirements.txt first.
    pause
    exit /b 1
)
"%RF_PYTHON%" "%~dp0launch.py" ai
set "RF_EXIT=%ERRORLEVEL%"
pause
exit /b %RF_EXIT%
