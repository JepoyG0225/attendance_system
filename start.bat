@echo off
title Student Attendance System
color 0A

echo =========================================
echo   Student Attendance System
echo =========================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.11+ from https://python.org
    pause
    exit /b
)

:: Install dependencies if needed
if not exist ".venv" (
    echo Setting up virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo Installing dependencies...
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo.
echo Starting server...
echo Open your browser at: http://localhost:8000
echo Press Ctrl+C to stop.
echo.

:: Start the FastAPI server
python -m uvicorn main:app --host 0.0.0.0 --port 8000

pause
