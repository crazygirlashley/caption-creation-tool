@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

where git >nul 2>nul
if errorlevel 1 (
    echo [update check] git not found on PATH -- skipping update check.
    goto :run
)

if not exist "%SCRIPT_DIR%.git" (
    echo [update check] not a git checkout -- skipping update check.
    goto :run
)

echo Checking for updates...
git fetch --quiet origin
if errorlevel 1 (
    echo [update check] could not reach GitHub -- continuing with current version.
    goto :run
)

git rev-parse @{u} >nul 2>nul
if errorlevel 1 (
    echo [update check] no upstream branch configured -- skipping update check.
    goto :run
)

for /f %%i in ('git rev-parse HEAD') do set "LOCAL_REV=%%i"
for /f %%i in ('git rev-parse @{u}') do set "REMOTE_REV=%%i"

if "%LOCAL_REV%"=="%REMOTE_REV%" (
    echo Already up to date.
) else (
    echo Updates found -- pulling latest from GitHub...
    git pull --ff-only
    if errorlevel 1 (
        echo [update check] could not update automatically -- you may have local
        echo changes. Continuing with the current version. Run "git status" to
        echo see what's going on.
    ) else (
        echo Updated successfully.
    )
)

:run
python "%SCRIPT_DIR%caption_creator.py"
pause
