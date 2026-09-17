@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0build_windows.ps1"
if errorlevel 1 (
  echo Build failed. Review the error above.
  pause
  exit /b 1
)
pause
