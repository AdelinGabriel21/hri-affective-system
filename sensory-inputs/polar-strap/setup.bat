@echo off
echo Creare mediu virtual pentru Polar Strap...
python -m venv venv
call venv\Scripts\activate
echo Instalare dependinte...
pip install --upgrade pip
pip install -r requirements.txt
echo Setup complet! Pentru a rula: call venv\Scripts\activate si apoi python polar_reader.py
pause