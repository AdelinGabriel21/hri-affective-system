@echo off
setlocal
echo ===================================================
echo [SETUP] Configurare mediu pentru Polar Verity Sense
echo ===================================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [EROARE] Python nu a fost gasit in PATH! Instaleaza Python 3.10+ si bifeaza 'Add to PATH'.
    pause
    exit /b 1
)

if not exist "venv" (
    echo Creare virtual environment...
    python -m venv venv
)

echo Activare mediu virtual si instalare dependinte...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist ".env" (
    echo Copiere sablon .env...
    copy .env.example .env
)

echo ===================================================
echo [SUCCES] Setup complet!
echo Pentru rulare: call venv\Scripts\activate ^&^& python polar_reader.py
echo ===================================================
pause