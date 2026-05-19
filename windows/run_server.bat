@echo off
:: Launcher for the Attendance System server.
:: Used by Desktop shortcut, Start Menu, and Task Scheduler auto-start.
:: Logs go to %LOCALAPPDATA%\AttendanceSystem\server.log

setlocal
set "APP_DIR=%~dp0.."
set "LOG_DIR=%LOCALAPPDATA%\AttendanceSystem"
set "LOG_FILE=%LOG_DIR%\server.log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

cd /d "%APP_DIR%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment is missing. Re-run install.bat as administrator.
    pause
    exit /b 1
)

echo =========================================
echo   Saint Joseph Academy Cuyo
echo   Student Attendance System - Server
echo =========================================
echo.
echo Dashboard: http://localhost:8000
echo Scanner 1: http://localhost:8000/scanner?id=1^&label=Entrance
echo Scanner 2: http://localhost:8000/scanner?id=2^&label=Exit
echo.
echo Log file:  %LOG_FILE%
echo Press Ctrl+C in this window to stop the server.
echo.

:: Stream stdout to console AND log file
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 >>"%LOG_FILE%" 2>&1
