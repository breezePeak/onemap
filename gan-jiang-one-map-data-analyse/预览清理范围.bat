@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0clean-data.ps1" -Preview
pause
