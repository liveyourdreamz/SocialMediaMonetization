@echo off
setlocal EnableExtensions
title Content Agent Portfolio
rem One file to install and run the app on a Windows laptop.
rem Double-click it, or run it from a terminal. Safe to run again: it updates and restarts.
rem This file only fetches the code; the steps that change over time live in tools\start.bat
rem and the update itself in src\cap\updater.py (run from the newest fetched commit).
rem The app asks for your Anthropic API key on first run (typed hidden) and writes it only
rem to the local .env file next to the code (git-ignored). It never goes anywhere but Anthropic.
rem An update never stops the app starting: if it fails, you get one warning and the
rem version already on this laptop starts.

rem ---- run from a copy in %TEMP% -------------------------------------------------------
rem cmd reads a batch file line by line while it runs, and the update can rewrite this file
rem (a copy of it lives in the app folder). The copy never changes; the relaunch and the
rem exit are one line, so nothing of this file is read again after the copy finishes.
if /i "%~1"=="--from-temp" goto :main
forfiles /p "%TEMP%" /m cap-install-*.bat /d -1 /c "cmd /c del @path" >nul 2>nul
set "CAP_SELF=%TEMP%\cap-install-%RANDOM%%RANDOM%.bat"
copy /y "%~f0" "%CAP_SELF%" >nul 2>nul && (call "%CAP_SELF%" --from-temp & exit /b)

:main
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

rem ---- git: on PATH, or installed but not on PATH, or install it -------------------------
where git >nul 2>nul && goto :have_git
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
where git >nul 2>nul && goto :have_git
if exist "%LocalAppData%\Programs\Git\cmd\git.exe" set "PATH=%LocalAppData%\Programs\Git\cmd;%PATH%"
where git >nul 2>nul && goto :have_git
echo  Installing Git...
winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
where git >nul 2>nul && goto :have_git
echo  Could not install Git automatically. Install it from https://git-scm.com and run this file again.
goto :fail
:have_git

rem ---- uv (Python project tool; brings its own Python) ----------------------------------
where uv >nul 2>nul || goto :install_uv
uv self update -q >nul 2>nul
goto :have_uv
:install_uv
echo  Installing uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul && goto :have_uv
echo  uv did not install. Close this window, open a new one and run this file again.
goto :fail
:have_uv

rem ---- the code ---------------------------------------------------------------------------
if not exist "%DIR%\.git" goto :clone
echo  Checking for a newer version...
rem Never wait for a person: no git prompt, no hidden Credential Manager window.
set "GIT_TERMINAL_PROMPT=0"
set "GCM_INTERACTIVE=never"
git -C "%DIR%" -c credential.interactive=never -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 fetch -q origin %BRANCH%
if errorlevel 1 goto :offline
set "UPDATER=%DIR%\.git\cap-updater.py"
git -C "%DIR%" show FETCH_HEAD:src/cap/updater.py > "%UPDATER%"
if errorlevel 1 goto :no_updater
if exist "%DIR%\.venv\Scripts\python.exe" goto :update_venv
uv run -q --no-project --python ">=3.12" python -I "%UPDATER%" --dir "%DIR%" --branch "%BRANCH%"
goto :updated
:update_venv
"%DIR%\.venv\Scripts\python.exe" -I "%UPDATER%" --dir "%DIR%" --branch "%BRANCH%"
:updated
set "RC=%errorlevel%"
if "%RC%"=="0" goto :start
rem 2: not updated, the updater printed why and the version on disk starts
if "%RC%"=="2" goto :start
if "%RC%"=="3" goto :already_running
echo  WARNING: the update step did not run (code %RC%). Starting the version already on this laptop.
goto :start

:no_updater
echo  WARNING: the newest version has no updater; starting the version already on this laptop.
goto :start

:offline
echo.
echo  WARNING: could not reach GitHub to check for a newer version (no internet, GitHub
echo  is down, or GitHub wants you to sign in again). Starting the version already on
echo  this laptop.
echo.
goto :start

:already_running
echo  Opening it in your browser instead of starting a second copy.
start "" "http://127.0.0.1:8000/"
if not defined CAP_SMOKE timeout /t 10
exit /b 0

:clone
echo  Downloading the code to %DIR% ...
git clone -q --branch %BRANCH% %REPO% "%DIR%"
if errorlevel 1 ( echo  Clone failed. Is the repository shared with your GitHub account? & goto :fail )

:start
cd /d "%DIR%"
rem ---- hand over to the copy that ships with the code (so this file never goes stale)
call "%DIR%\tools\start.bat"
exit /b

:fail
if not defined CAP_SMOKE pause
exit /b 1
