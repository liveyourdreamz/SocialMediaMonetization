@echo off
rem One real post per channel, idea to published file, using your API key.
rem Nothing goes to your queue or to YouTube; it says exactly where any run stops.
cd /d "%~dp0"
git pull -q 2>nul
uv run cap check --full %*
pause
