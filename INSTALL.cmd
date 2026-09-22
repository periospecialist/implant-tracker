@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update.ps1" -Source "%~dp0"
if errorlevel 1 (
  echo Installation did not finish. Review the message above.
  pause
  exit /b 1
)
