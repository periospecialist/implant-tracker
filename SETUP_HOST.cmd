@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0setup_host.ps1"
if errorlevel 1 (
  echo Setup did not finish. Review the error above.
  pause
  exit /b 1
)
pause
