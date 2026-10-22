@echo off
rem Upload a 5-second PRIVATE test clip to your connected YouTube channel.
rem Proves the sign-in and the upload path; nothing public. Prints the link.
rem Put this file in your app folder (the one with install.bat and check.bat).
cd /d "%~dp0"
if not exist pyproject.toml (
  echo This file must be in the app folder, next to check.bat. Move it there and run it again.
  pause
  exit /b 1
)
echo Updating the app to the latest build...
git pull -q --ff-only 2>nul
for /f "delims=" %%v in ('git log -1 "--format=%%h"') do echo Build %%v
uv run cap youtube test
pause
