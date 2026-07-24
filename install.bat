@echo off
setlocal enabledelayedexpansion

rem ---------------------------------------------------------------------
rem Caption Creator - single-file installer
rem
rem Downloads the latest built release (no Python/Git required on this
rem machine) into a location you choose, and optionally adds a Desktop
rem shortcut. The shortcut points at CaptionCreatorLauncher.exe, which
rem checks for updates on every launch and only then starts the app.
rem ---------------------------------------------------------------------

set "DEFAULT_INSTALL_DIR=%LOCALAPPDATA%\Programs\CaptionCreator"
set "RELEASE_ZIP_URL=https://github.com/crazygirlashley/caption-creation-tool/releases/latest/download/CaptionCreator-win64.zip"

echo ============================================
echo  Caption Creator - Installer
echo ============================================
echo.

where curl >nul 2>nul
if errorlevel 1 (
    echo ERROR: curl was not found on PATH ^(ships with Windows 10 1803+ and
    echo Windows 11 by default^). Update Windows or install curl, then re-run
    echo this installer.
    pause
    exit /b 1
)

echo Install location ^(enter to use the default, no quotes^):
set "INSTALL_DIR="
set /p "INSTALL_DIR=  [%DEFAULT_INSTALL_DIR%]: "
if "%INSTALL_DIR%"=="" set "INSTALL_DIR=%DEFAULT_INSTALL_DIR%"

echo.
echo Install location: %INSTALL_DIR%
echo.

if exist "%INSTALL_DIR%" (
    if not exist "%INSTALL_DIR%\CaptionCreatorLauncher.exe" (
        dir /b "%INSTALL_DIR%" 2>nul | findstr "." >nul
        if not errorlevel 1 (
            echo WARNING: %INSTALL_DIR%
            echo already exists and doesn't look like a Caption Creator install.
            set "CONFIRM_DIR=N"
            set /p "CONFIRM_DIR=Install here anyway? [y/N] "
            if /i not "!CONFIRM_DIR:~0,1!"=="Y" (
                echo Cancelled.
                pause
                exit /b 1
            )
        )
    )
)

echo Downloading Caption Creator...
set "TMP_ZIP=%TEMP%\CaptionCreator-win64.zip"
if exist "%TMP_ZIP%" del /f /q "%TMP_ZIP%" >nul 2>nul
curl -L --fail -o "%TMP_ZIP%" "%RELEASE_ZIP_URL%"
if errorlevel 1 (
    echo ERROR: Failed to download Caption Creator. Check your internet
    echo connection and try again, or make sure a release has been published
    echo at https://github.com/crazygirlashley/caption-creation-tool/releases
    pause
    exit /b 1
)

echo Installing to %INSTALL_DIR%...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
powershell -NoProfile -Command "Expand-Archive -Path '%TMP_ZIP%' -DestinationPath '%INSTALL_DIR%' -Force"
if errorlevel 1 (
    echo ERROR: Failed to extract the downloaded archive.
    pause
    exit /b 1
)
del /f /q "%TMP_ZIP%" >nul 2>nul

rem Expand-Archive -Force only overwrites files present in the zip (the app
rem exes + _internal/ + version.txt) -- formats/, watermark/, DA credentials,
rem and the crash log from a previous install are left untouched either way,
rem same as the launcher's own update step.

echo.
echo Caption Creator is installed at:
echo   %INSTALL_DIR%
echo.

set "ADD_SHORTCUT=Y"
set /p "ADD_SHORTCUT=Add a Desktop shortcut? [Y/n] "
if /i "%ADD_SHORTCUT:~0,1%"=="N" goto :skip_shortcut

echo Creating Desktop shortcut...
powershell -NoProfile -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut($d + '\Caption Creator.lnk'); $s.TargetPath='%INSTALL_DIR%\CaptionCreatorLauncher.exe'; $s.WorkingDirectory='%INSTALL_DIR%'; $s.Description='Launch Caption Creator (checks for updates first)'; $s.Save()"
if errorlevel 1 (
    echo WARNING: Could not create the Desktop shortcut. You can still launch
    echo Caption Creator by running CaptionCreatorLauncher.exe in the install
    echo folder above.
) else (
    echo Desktop shortcut created.
)
:skip_shortcut

echo.
echo ============================================
echo  Done!
echo ============================================
echo Launch Caption Creator any time via the Desktop shortcut, or by running:
echo   %INSTALL_DIR%\CaptionCreatorLauncher.exe
pause
exit /b 0
