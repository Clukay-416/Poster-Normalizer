@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_offline.ps1"
if errorlevel 1 exit /b 1
"runtime\python\python.exe" "app\scripts\install_ai.py"
if errorlevel 1 (
  echo AI installation failed. See the message above.
  pause
  exit /b 1
)
echo Offline AI runtimes installed. Run start.cmd to open the model center.
pause
