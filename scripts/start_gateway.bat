@echo off
setlocal
cd /d "%~dp0..\gateway"
echo Starting RF Localization Gateway...
python main.py
echo.
echo Gateway stopped.
pause
