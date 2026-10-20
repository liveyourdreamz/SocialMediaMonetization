@echo off
setlocal EnableExtensions
title Content Agent Portfolio
rem One file to install and run the app on a Windows laptop.
rem Double-click it, or run it from a terminal. Safe to run again: it updates and restarts.
rem Your Anthropic API key is asked for here, typed masked, and written only to the local
rem .env file next to the code (git-ignored). It is never sent anywhere but Anthropic.

set "REPO=https://github.com/liveyourdreamz/SocialMediaMonetization.git"
set "BRANCH=bootstrap"
set "DIR=%USERPROFILE%\SocialMediaMonetization"

echo.
echo  Content Agent Portfolio - install and run
echo  ==========================================
echo.

rem ---- git -----------------------------------------------------------------------
where git >nul 2>nul
if errorlevel 1 (
  echo  Installing Git...
  winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo  Could not install Git automatically. Install it from https://git-scm.com and run this file again.
    pause & exit /b 1
  )
  set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
)

rem ---- uv (Python project tool; brings its own Python) ---------------------------
where uv >nul 2>nul
if errorlevel 1 (
  echo  Installing uv...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>nul
if errorlevel 1 (
  echo  uv did not install. Close this window, open a new one and run this file again.
  pause & exit /b 1
)

rem ---- the code ------------------------------------------------------------------
if exist "%DIR%\.git" (
  echo  Updating the code in %DIR% ...
  git -C "%DIR%" fetch -q origin %BRANCH%
  git -C "%DIR%" checkout -q %BRANCH%
  git -C "%DIR%" pull -q --ff-only origin %BRANCH%
) else (
  echo  Downloading the code to %DIR% ...
  git clone -q --branch %BRANCH% %REPO% "%DIR%"
  if errorlevel 1 ( echo  Clone failed. Is the repository shared with your GitHub account? & pause & exit /b 1 )
)
cd /d "%DIR%"

rem ---- .env and the API key ------------------------------------------------------
if not exist ".env" copy /y ".env.example" ".env" >nul

findstr /b /c:"CAP_ANTHROPIC_API_KEY=sk-" ".env" >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Anthropic API key. Typed masked; saved only to %DIR%\.env; leave blank for mock mode.
  for /f "usebackq delims=" %%k in (`powershell -NoProfile -Command "$s = Read-Host '  API key' -AsSecureString; $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [Runtime.InteropServices.Marshal]::PtrToStringAuto($p)"`) do set "KEY=%%k"
  if defined KEY (
    powershell -NoProfile -Command "$e = Get-Content .env | Where-Object { $_ -notmatch '^#?\s*CAP_ANTHROPIC_API_KEY=' -and $_ -notmatch '^CAP_MODEL_BACKEND=' }; $e += 'CAP_MODEL_BACKEND=anthropic'; $e += ('CAP_ANTHROPIC_API_KEY=' + $env:KEY); Set-Content .env $e"
    echo  Saved. Drafts and judges will run on the real models.
  ) else (
    echo  No key: running in mock mode. Run this file again any time to add one.
  )
  set "KEY="
)

rem ---- run -----------------------------------------------------------------------
echo.
echo  Installing dependencies (first time takes a minute)...
uv sync --quiet
if errorlevel 1 ( echo  Dependency install failed. & pause & exit /b 1 )

echo.
echo  Starting. The browser opens at http://127.0.0.1:8000/login - sign in as hemant.
echo  On your phone, same Wi-Fi: http://^<this laptop's address^>:8000/login
echo  Press Ctrl+C in this window to stop.
echo.
uv run cap serve --demo --host 0.0.0.0 --open
pause
