@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0clean-data.ps1" %*
set "cleanup_exit=%errorlevel%"
pause
exit /b %cleanup_exit%
