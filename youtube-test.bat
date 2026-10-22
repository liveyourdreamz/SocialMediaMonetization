@echo off
rem Upload a 5-second PRIVATE test clip to your connected YouTube channel.
rem Proves the sign-in and the upload path; nothing public. Prints the link.
cd /d "%~dp0"
uv run cap youtube test
pause
