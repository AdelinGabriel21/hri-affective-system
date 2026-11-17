@echo off
rem Run from Windows: run.bat [bridge|test|send]. Runs run.sh inside WSL.
cd /d "%~dp0"
wsl.exe bash ./run.sh %*
