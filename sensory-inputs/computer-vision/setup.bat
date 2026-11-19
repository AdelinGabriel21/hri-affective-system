@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Computer vision setup (Windows)
echo ============================================

REM --- 1. Find Python (3.10 / 3.11 work best with DeepFace + TensorFlow) ---
set "PYTHON_CMD="
py -3.11 --version >nul 2>&1 && set "PYTHON_CMD=py -3.11"
if not defined PYTHON_CMD (
    py -3.10 --version >nul 2>&1 && set "PYTHON_CMD=py -3.10"
)
if not defined PYTHON_CMD (
    python --version >nul 2>&1 && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo [ERROR] Python not found. Install Python 3.10 or 3.11 from python.org and tick "Add to PATH".
    pause
    exit /b 1
)
echo [SETUP] Using: %PYTHON_CMD%

REM --- 2. Create the virtual environment ---
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment .venv ...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

REM --- 3. Install dependencies ---
echo [SETUP] Upgrading pip ...
"%VENV_PY%" -m pip install --upgrade pip

echo [SETUP] Installing requirements.txt (the first time this takes several minutes) ...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Installing requirements failed. Read the message above.
    pause
    exit /b 1
)

REM --- 4. Fix OpenCV: other packages pull in headless builds that have no window support ---
echo [SETUP] Repairing OpenCV (removing headless builds) ...
"%VENV_PY%" -m pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python opencv-contrib-python-headless
"%VENV_PY%" -m pip install opencv-python==4.11.0.86
if errorlevel 1 (
    echo [ERROR] Installing opencv-python failed.
    pause
    exit /b 1
)

REM --- 5. Create a default .env if there is none ---
if not exist ".env" (
    echo [SETUP] Creating .env with default settings ...
    (
        echo MQTT_HOST=hri.thecraceatree.com
        echo MQTT_PORT=443
        echo MQTT_TOPIC=telemetry/cv
        echo CAMERA_INDEX=0
        echo # MQTT_USERNAME=
        echo # MQTT_PASSWORD=
        echo # ALLOW_DISAPPOINTMENT=false
    ) > .env
)

REM --- 6. Verify ---
echo [SETUP] Checking OpenCV ...
"%VENV_PY%" -c "import cv2; print('OpenCV', cv2.__version__)"

echo.
echo ============================================
echo  Setup finished.
echo  Run with:
echo    .venv\Scripts\python.exe vision_affect.py
echo  (in VS Code: Ctrl+Shift+P - Python: Select Interpreter - pick .venv)
echo ============================================
pause
endlocal
