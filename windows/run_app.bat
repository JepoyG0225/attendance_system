@echo off
:: ============================================================================
::  Native desktop launcher for the Attendance System.
::  Opens the dashboard inside a pywebview window (taskbar app, no browser).
::  The server still listens on TCP 8000 so Pi scanners on the LAN work.
::  Used by the Desktop shortcut and Start Menu shortcut.
:: ============================================================================
setlocal
set "APP_DIR=%~dp0.."
set "LOG_DIR=%LOCALAPPDATA%\AttendanceSystem"
set "LOG_FILE=%LOG_DIR%\app.log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

cd /d "%APP_DIR%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment is missing.
    echo Re-run the installer Setup.exe ^(or windows\install.bat as administrator^).
    pause
    exit /b 1
)

:: pythonw.exe = no console window. python.exe would show a black box behind the app.
start "" ".venv\Scripts\pythonw.exe" "app.py"
