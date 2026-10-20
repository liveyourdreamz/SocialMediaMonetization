@echo off
rem Everything after "get the code": API key, dependencies, run. Lives in the repo so
rem it updates with the code; install.bat (the downloaded bootstrap) hands over to it.
cd /d "%~dp0.."

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\setkey.ps1"
if errorlevel 1 ( echo  Could not write .env. & pause & exit /b 1 )

echo.
echo  Installing dependencies (first time takes a minute)...
uv sync --quiet
if errorlevel 1 ( echo  Dependency install failed. & pause & exit /b 1 )

echo.
findstr /b /c:"CAP_MODEL_BACKEND=anthropic" ".env" >nul 2>nul && (echo  Model backend: anthropic ^(real drafts^)) || (echo  Model backend: mock ^(run set-key.bat to add your key^))
echo  Starting. The browser opens at http://127.0.0.1:8000/login - sign in as hemant.
echo  On your phone, same Wi-Fi: http://^<this laptop's address^>:8000/login
echo  Press Ctrl+C in this window to stop.
echo.
uv run cap serve --demo --host 0.0.0.0 --open
pause
