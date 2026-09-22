@echo off
cd /d "%~dp0"
rem The trailing dot prevents the directory's final backslash from escaping the
rem closing quote when powershell.exe parses the native Windows command line.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update.ps1" -Source "%~dp0."
if errorlevel 1 (
  echo Installation did not finish. Review the message above.
  pause
  exit /b 1
)
