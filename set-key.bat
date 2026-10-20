@echo off
rem Change the Anthropic API key in the local .env (typed masked). Restart the app after.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\setkey.ps1" -Force
pause
