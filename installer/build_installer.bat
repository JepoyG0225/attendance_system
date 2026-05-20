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

echo Using compiler: %ISCC%
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
