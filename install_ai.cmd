@echo off
cd /d "%~dp0"
call install_offline.cmd
if errorlevel 1 exit /b 1
"runtime\python\python.exe" "app\scripts\install_ai.py"
if errorlevel 1 (
  echo AI installation failed. See the message above.
  pause
  exit /b 1
)
echo Offline AI runtimes installed. Run start.cmd to open the model center.
pause
