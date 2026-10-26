@echo off
rem Remake your newest published post with real photographs and play it beside the
rem video it has now. Nothing in the app changes. Takes a few minutes and a little
rem API credit. To remake a particular post: visuals-trial.bat <post id>
rem It runs the code already in this folder: run install.bat first for the newest build.
cd /d "%~dp0"
if not exist pyproject.toml (
  echo This file must be in the app folder, next to check.bat. Move it there and run it again.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('git log -1 "--format=%%h"') do echo Build %%v ^(run install.bat to get the newest^)
if "%~1"=="" (uv run cap visuals trial) else (uv run cap visuals trial --post %1)
pause
