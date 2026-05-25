@echo off
:: ============================================================================
::  Build script - compiles AttendanceSystem.iss into a Setup.exe
::  Run this from the installer\ folder after you've installed Inno Setup 6.
::  Output: installer\Output\AttendanceSystem_Setup_<version>.exe
:: ============================================================================
setlocal EnableExtensions

cd /d "%~dp0"

:: ── Locate the Inno Setup compiler ──────────────────────────────────────────
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%ProgramFiles%\Inno Setup 5\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 5\ISCC.exe"
) do (
    if exist %%~P set "ISCC=%%~P"
)

if not defined ISCC where iscc.exe >nul 2>&1 && set "ISCC=iscc.exe"

if not defined ISCC (
    echo.
    echo [ERROR] Inno Setup compiler ^(ISCC.exe^) was not found.
    echo.
    echo Install Inno Setup 6 first:
    echo   https://jrsoftware.org/isdl.php
    echo.
    echo After install, re-run this build script.
    echo.
    pause
    exit /b 1
)

:: ── Pre-flight: required bundled assets ─────────────────────────────────────
if not exist "drivers\CH341SER.EXE" (
    echo.
    echo [ERROR] Missing required driver file:
    echo     installer\drivers\CH341SER.EXE
    echo.
    echo The CH340 USB-serial driver is bundled with the installer so school
    echo PCs can talk to SIM800L-based GSM modems out of the box.
    echo.
    echo Download from:
    echo   https://www.wch-ic.com/downloads/CH341SER_EXE.html
    echo.
    echo Save the file at the path above, then re-run this build script.
    echo.
    pause
    exit /b 1
)

if not exist "python\python-3.11.9-amd64.exe" (
    echo.
    echo [ERROR] Missing bundled Python runtime:
    echo     installer\python\python-3.11.9-amd64.exe
    echo.
    echo Python is bundled so school PCs don't need a pre-existing install.
    echo.
    echo Download from:
    echo   https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo.
    echo Save the file at the path above, then re-run this build script.
    echo.
    pause
    exit /b 1
)

:: Wheel cache — required so `pip install -r requirements.txt` works fully
:: offline on school PCs with no internet. Generate with Python 3.11:
::   py -3.11 -m pip download -r ..\requirements.txt -d wheels\ --prefer-binary
set "WHEEL_COUNT=0"
for %%F in ("wheels\*.whl" "wheels\*.tar.gz") do set /a WHEEL_COUNT+=1
if %WHEEL_COUNT% LSS 10 (
    echo.
    echo [ERROR] Missing or empty wheel cache:
    echo     installer\wheels\
    echo.
    echo Found only %WHEEL_COUNT% wheel files - need ~40 for offline install.
    echo.
    echo Regenerate with Python 3.11 installed:
    echo   py -3.11 -m pip download -r ..\requirements.txt -d wheels\ --prefer-binary
    echo.
    echo Without this, the school PC needs internet during install.
    echo.
    pause
    exit /b 1
)

echo Using compiler: %ISCC%
echo Driver bundled: drivers\CH341SER.EXE
echo Python bundled: python\python-3.11.9-amd64.exe
echo Wheels bundled: %WHEEL_COUNT% files in wheels\
echo.

:: ── Compile ─────────────────────────────────────────────────────────────────
"%ISCC%" "AttendanceSystem.iss"
if errorlevel 1 (
    echo.
    echo [ERROR] Compilation failed - see messages above.
    pause
    exit /b 1
)

echo.
echo ============================================================================
echo   Build complete!
echo.
for %%F in ("Output\AttendanceSystem_Setup_*.exe") do (
    echo   Output: %%~fF
    echo   Size:   %%~zF bytes
)
echo ============================================================================
echo.
echo Distribute the Setup.exe to the school PC.
echo The user runs it once - it installs everything as administrator.
echo.
pause
