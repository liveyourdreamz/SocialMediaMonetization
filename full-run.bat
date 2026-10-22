@echo off
rem One real post per channel, idea to published file, using your API key.
rem Nothing goes to your queue or to YouTube; it says exactly where any run stops.
rem It runs the code already in this folder: install.bat is the one place that updates it
rem (an update here would rewrite this file while cmd is reading it).
cd /d "%~dp0"
uv run cap check --full %*
pause
