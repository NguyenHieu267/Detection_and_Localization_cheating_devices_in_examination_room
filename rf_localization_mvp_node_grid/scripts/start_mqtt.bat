@echo off
setlocal
set "MOSQUITTO=C:\Program Files\mosquitto\mosquitto.exe"
set "CONFIG=%~dp0mosquitto.conf"

if not exist "%MOSQUITTO%" (
    echo ERROR: Mosquitto not found:
    echo %MOSQUITTO%
    pause
    exit /b 1
)

echo Starting RF Localization MQTT broker...
"%MOSQUITTO%" -c "%CONFIG%" -v

echo.
echo Mosquitto stopped.
pause
