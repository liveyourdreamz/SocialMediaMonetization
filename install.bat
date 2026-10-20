@echo off
setlocal EnableExtensions
title Content Agent Portfolio
rem One file to install and run the app on a Windows laptop.
rem Double-click it, or run it from a terminal. Safe to run again: it updates and restarts.
rem This file only fetches the code; the steps that change over time live in tools\start.bat.
rem The app asks for your Anthropic API key on first run (typed hidden) and writes it only
rem to the local .env file next to the code (git-ignored). It never goes anywhere but Anthropic.

set "REPO=https://github.com/liveyourdreamz/SocialMediaMonetization.git"
set "BRANCH=bootstrap"
set "DIR=%USERPROFILE%\SocialMediaMonetization"
rem CI runs this file against the commit under test
if defined CAP_BRANCH set "BRANCH=%CAP_BRANCH%"
if defined CAP_DIR set "DIR=%CAP_DIR%"

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
    goto :fail
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
  goto :fail
)

rem ---- the code ------------------------------------------------------------------
if exist "%DIR%\.git" (
  echo  Updating the code in %DIR% ...
  git -C "%DIR%" fetch -q origin %BRANCH%
  git -C "%DIR%" checkout -q %BRANCH%
  git -C "%DIR%" pull -q --ff-only origin %BRANCH%
  if errorlevel 1 ( echo  Update failed - the messages above say why. & goto :fail )
) else (
  echo  Downloading the code to %DIR% ...
  git clone -q --branch %BRANCH% %REPO% "%DIR%"
  if errorlevel 1 ( echo  Clone failed. Is the repository shared with your GitHub account? & goto :fail )
)
cd /d "%DIR%"

rem ---- hand over to the copy that ships with the code (so this file never goes stale)
call "%DIR%\tools\start.bat"
exit /b

:fail
if not defined CAP_SMOKE pause
exit /b 1
