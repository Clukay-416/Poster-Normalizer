@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_offline.ps1"
if errorlevel 1 (
    echo Installation failed. Keep the error message for troubleshooting.
    pause
    exit /b 1
)
echo Offline installation complete. Run start.cmd to open the application.
pause
