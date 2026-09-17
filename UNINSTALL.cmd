@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
if errorlevel 1 (
  echo Uninstall did not finish. Review the message above.
  pause
  exit /b 1
)
pause
