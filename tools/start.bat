@echo off
rem Everything after "get the code". Lives in the repo so it updates with the code;
rem install.bat (the downloaded bootstrap) hands over to it. The API key is asked for
rem by the app itself on first run (uv run cap key changes it later).
cd /d "%~dp0.."
echo.
echo  Installing dependencies (first time takes a minute)...
uv sync --quiet
if errorlevel 1 ( echo  Dependency install failed. & pause & exit /b 1 )
echo.
uv run cap serve --demo --host 0.0.0.0 --open %*
pause
