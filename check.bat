@echo off
rem Check this laptop against the real services: key, model, voice, ffmpeg, fonts, fetch.
cd /d "%~dp0"
uv run cap check
pause
