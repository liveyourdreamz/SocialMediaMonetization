@echo off
rem Upload a 5-second PRIVATE test clip to your connected YouTube channel.
rem Proves the sign-in and the upload path; nothing public. Prints the link.
rem It runs the code already in this folder: run install.bat first for the newest build
rem (only install.bat updates; an update here would rewrite this file while it runs).
cd /d "%~dp0"
if not exist pyproject.toml (
  echo This file must be in the app folder, next to check.bat. Move it there and run it again.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('git log -1 "--format=%%h"') do echo Build %%v ^(run install.bat to get the newest^)
uv run cap youtube test
pause
