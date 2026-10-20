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
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\setkey.ps1"
if errorlevel 1 ( echo  Could not write .env. & pause & exit /b 1 )

rem ---- run -----------------------------------------------------------------------
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
