@echo off
rem Everything after "get the code". Lives in the repo so it updates with the code;
rem install.bat (the downloaded bootstrap) hands over to it. The API key is asked for
rem by the app itself on first run (uv run cap key changes it later).
cd /d "%~dp0.."
echo.
echo  Installing dependencies (first time takes a minute)...
uv sync --quiet
if errorlevel 1 ( echo  Dependency install failed. & goto :fail )
echo.
if defined CAP_SMOKE (
  rem CI: run the whole loop through the real app instead of serving (see cap smoke)
  uv run cap smoke
  exit /b
)
echo  Checking this laptop against the real services (key, model, voice, ffmpeg)...
uv run cap check
if errorlevel 1 (
  echo.
  echo  A check FAILED above. The app still starts, but briefs will park until it is fixed.
  echo.
)
uv run cap serve --demo --host 0.0.0.0 --open %*
pause
exit /b

:fail
if not defined CAP_SMOKE pause
exit /b 1
