@echo off
rem Everything after "get the code". Lives in the repo so it updates with the code;
rem install.bat (the downloaded bootstrap) hands over to it. The API key is asked for
rem by the app itself on first run (uv run cap key changes it later).
cd /d "%~dp0.."
echo.
echo  Installing dependencies (first time takes a minute)...
uv sync --quiet
if errorlevel 1 ( if exist ".venv\Scripts\python.exe" ( echo  WARNING: could not update the dependencies; starting with the ones already installed. ) else ( echo  Dependency install failed and nothing is installed yet. & goto :fail ) )
for /f "delims=" %%v in ('git log -1 "--format=%%h %%cd" "--date=format:%%d %%b %%H:%%M"') do echo  Code updated to build %%v
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
echo.
echo  Run the full test now? One real 30-second post per channel, idea to published file,
echo  using your API key (a few minutes, a little API credit). Nothing goes to YouTube.
choice /C YN /T 15 /D N /M "  Press Y to run it, N (or wait 15 seconds) to start the app"
if errorlevel 2 goto :serve
echo.
uv run cap check --full
echo.
echo  Full test finished: the lines above say ok or FAIL per channel. Starting the app...
:serve
uv run cap serve --demo --host 0.0.0.0 --open %*
pause
exit /b

:fail
if not defined CAP_SMOKE pause
exit /b 1
