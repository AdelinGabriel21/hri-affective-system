@echo off
setlocal
echo ===================================================
echo [SETUP] Setting up Computer Vision Module
echo ===================================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH! Please install Python 3.10/3.11.
    pause
    exit /b 1
)

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment and installing dependencies...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist ".env" (
    echo Creating default .env file...
    (
        echo MQTT_HOST="hri.thecraceatree.com"
        echo MQTT_PORT=443
        echo MQTT_TOPIC="telemetry/cv"
        echo CAMERA_INDEX=0
    ) > .env
)

echo ===================================================
echo [SUCCESS] Setup complete!
echo To run the CV module:
echo   call venv\Scripts\activate
echo   python vision_affect.py
echo ===================================================
pause