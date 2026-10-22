@echo off
rem Enter or change the Anthropic API key (typed hidden, saved to the local .env only).
cd /d "%~dp0"
uv run cap key
pause
