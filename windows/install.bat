@echo off
:: ────────────────────────────────────────────────────────────────────────────
:: Attendance System - Windows Installer
:: Sets up venv, firewall, Task Scheduler auto-start, Desktop + Start Menu
:: shortcuts. Run as administrator.
:: ────────────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
title Attendance System - Installer

:: ── Auto-elevate to administrator ─────────────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

:: ── Locate the app directory (parent of this script) ─────────────────────
set "APP_DIR=%~dp0.."
pushd "%APP_DIR%"
set "APP_DIR=%CD%"
popd

echo =========================================
echo   Attendance System - Installer
echo =========================================
echo.
echo Install location: %APP_DIR%
echo.

:: ── 1. Check Python ───────────────────────────────────────────────────────
echo [1/6] Checking for Python 3.11+ ...
where py >nul 2>&1
if errorlevel 1 (
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [ERROR] Python is not installed.
        echo Please install Python 3.11 or newer from:
        echo     https://www.python.org/downloads/windows/
        echo.
        echo During install, tick "Add python.exe to PATH".
        echo Then re-run this installer.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=python"
) else (
    set "PYTHON_CMD=py -3"
)
%PYTHON_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if errorlevel 1 (
    echo [ERROR] Need Python 3.11 or newer.
    %PYTHON_CMD% --version
    pause
    exit /b 1
)
%PYTHON_CMD% --version

:: ── 2. Virtual environment + dependencies ────────────────────────────────
echo.
echo [2/6] Setting up virtual environment...
if exist ".venv" (
    echo     .venv already exists - reusing.
) else (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

echo     Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet

echo     Installing requirements (this may take a few minutes)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. Are you connected to the internet?
    pause
    exit /b 1
)

:: ── 3. Initialize database ────────────────────────────────────────────────
echo.
echo [3/6] Initializing database...
".venv\Scripts\python.exe" -c "from database import init_db; init_db()"
if errorlevel 1 (
    echo [WARNING] DB init failed - it may already exist. Continuing.
)

:: ── 4. Windows Firewall: allow inbound TCP 8000 ──────────────────────────
echo.
echo [4/6] Configuring Windows Firewall (port 8000 inbound)...
netsh advfirewall firewall delete rule name="Attendance System" >nul 2>&1
netsh advfirewall firewall add rule name="Attendance System" ^
    dir=in action=allow protocol=TCP localport=8000 profile=any >nul
if errorlevel 1 (
    echo [WARNING] Could not add firewall rule. Add it manually:
    echo     netsh advfirewall firewall add rule name="Attendance System" dir=in action=allow protocol=TCP localport=8000
) else (
    echo     Firewall rule added: TCP 8000 inbound allowed.
)

:: ── 5. Task Scheduler: auto-start at boot ────────────────────────────────
echo.
echo [5/6] Registering auto-start at boot (Task Scheduler)...
schtasks /delete /tn "Attendance System" /f >nul 2>&1
schtasks /create /tn "Attendance System" ^
    /tr "\"%APP_DIR%\windows\run_server.bat\"" ^
    /sc onstart /rl highest /ru SYSTEM /f >nul
if errorlevel 1 (
    echo [WARNING] Task Scheduler entry could not be created.
    echo You can still start the server manually using the Desktop shortcut.
) else (
    echo     Server will auto-start on next Windows boot.
)

:: ── 6. Desktop + Start Menu shortcuts ────────────────────────────────────
echo.
echo [6/6] Creating shortcuts...
set "SHORTCUT_PS=%TEMP%\make_shortcut.ps1"
> "%SHORTCUT_PS%" echo $WshShell = New-Object -ComObject WScript.Shell
>> "%SHORTCUT_PS%" echo $Desktop = [System.Environment]::GetFolderPath('CommonDesktopDirectory')
>> "%SHORTCUT_PS%" echo $StartMenu = [System.Environment]::GetFolderPath('CommonPrograms')
>> "%SHORTCUT_PS%" echo $TargetBat = '%APP_DIR%\windows\run_server.bat'
>> "%SHORTCUT_PS%" echo $WorkDir   = '%APP_DIR%'
>> "%SHORTCUT_PS%" echo foreach ($dir in @($Desktop, $StartMenu)) {
>> "%SHORTCUT_PS%" echo   $lnk = Join-Path $dir 'Attendance Server.lnk'
>> "%SHORTCUT_PS%" echo   $s = $WshShell.CreateShortcut($lnk)
>> "%SHORTCUT_PS%" echo   $s.TargetPath = $TargetBat
>> "%SHORTCUT_PS%" echo   $s.WorkingDirectory = $WorkDir
>> "%SHORTCUT_PS%" echo   $s.IconLocation = 'shell32.dll,21'
>> "%SHORTCUT_PS%" echo   $s.Save()
>> "%SHORTCUT_PS%" echo }
>> "%SHORTCUT_PS%" echo $dashLnk = Join-Path $Desktop 'Attendance Dashboard.url'
>> "%SHORTCUT_PS%" echo Set-Content -Path $dashLnk -Value "[InternetShortcut]`nURL=http://localhost:8000"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SHORTCUT_PS%" >nul 2>&1
del "%SHORTCUT_PS%" >nul 2>&1
echo     Desktop and Start Menu shortcuts created.

:: ── Done ──────────────────────────────────────────────────────────────────
echo.
echo =========================================
echo   Installation Complete
echo =========================================
echo.
echo Next steps:
echo   1. Plug in your USB GSM modem.
echo   2. Double-click "Attendance Server" on the Desktop to start now,
echo      or restart Windows and it will auto-start.
echo   3. Open "Attendance Dashboard" to verify.
echo.
echo Dashboard URL: http://localhost:8000
echo.
pause
