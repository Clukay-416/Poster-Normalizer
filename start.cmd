@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_offline.ps1"
if errorlevel 1 goto failed
set "POSTER_INSTALL_ROOT=%~dp0"
"%~dp0runtime\python\python.exe" "%~dp0app\launcher.py"
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Please keep this window and share the error message.
pause
exit /b 1
