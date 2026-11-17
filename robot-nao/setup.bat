@echo off
rem One-time setup from Windows: runs setup.sh inside WSL (qi only exists for Linux).
setlocal
cd /d "%~dp0"

wsl.exe --status >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] WSL is not installed. In an admin PowerShell run: wsl --install
    pause
    exit /b 1
)

wsl.exe bash ./setup.sh
if %errorlevel% equ 10 (
    echo [SETUP] Restarting WSL to apply mirrored networking...
    wsl.exe --shutdown
    wsl.exe bash ./setup.sh
)
pause
