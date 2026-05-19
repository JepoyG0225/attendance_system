@echo off
:: ────────────────────────────────────────────────────────────────────────────
:: Attendance System - Windows Uninstaller
:: Removes auto-start, firewall rule, and shortcuts.
:: Does NOT delete the .venv or the attendance.db database (in case you want
:: to keep student data). Delete the project folder manually if you want a
:: full removal.
:: ────────────────────────────────────────────────────────────────────────────

setlocal
title Attendance System - Uninstaller

:: ── Auto-elevate ──────────────────────────────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

echo =========================================
echo   Attendance System - Uninstaller
echo =========================================
echo.
echo This will remove:
echo   - Auto-start at boot
echo   - Windows Firewall rule for port 8000
echo   - Desktop and Start Menu shortcuts
echo.
echo It will NOT delete the database (attendance.db) or your settings.
echo.
choice /c YN /m "Proceed"
if errorlevel 2 exit /b

echo.
echo Removing Task Scheduler entry...
schtasks /delete /tn "Attendance System" /f >nul 2>&1

echo Removing firewall rule...
netsh advfirewall firewall delete rule name="Attendance System" >nul 2>&1

echo Removing shortcuts...
del /q "%PUBLIC%\Desktop\Attendance Server.lnk" >nul 2>&1
del /q "%PUBLIC%\Desktop\Attendance Dashboard.url" >nul 2>&1
del /q "%ProgramData%\Microsoft\Windows\Start Menu\Programs\Attendance Server.lnk" >nul 2>&1

echo.
echo Done. The project folder and database have been left in place.
echo Delete the project folder manually if you want full removal.
echo.
pause
