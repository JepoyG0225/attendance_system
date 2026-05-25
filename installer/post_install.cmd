@echo off
:: ============================================================================
::  Post-install helper invoked by the Inno Setup [Run] section.
::  Runs hidden, so any output goes to a log file the user can inspect.
::
::  Usage:
::    post_install.cmd venv   "<APP_DIR>"   - create venv and install deps
::    post_install.cmd initdb "<APP_DIR>"   - initialize the SQLite DB
:: ============================================================================
setlocal EnableExtensions EnableDelayedExpansion

set "ACTION=%~1"
set "APP_DIR=%~2"

if "%APP_DIR%"=="" (
    echo [post_install] Missing APP_DIR argument & exit /b 1
)

set "LOG_DIR=%LOCALAPPDATA%\AttendanceSystem"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\install.log"

call :log "============================================"
call :log "post_install.cmd action=%ACTION% app=%APP_DIR%"
call :log "============================================"

:: -- Locate Python 3.11 specifically -----------------------------------------
:: The wheels bundled with this installer are pinned to cp311 / win_amd64, so
:: we MUST create the venv with Python 3.11. Split the interpreter into an
:: EXE (always quoted on use because it might be a path with spaces, e.g.
:: C:\Program Files\Python311\python.exe) and ARGS (unquoted, e.g. -3.11
:: for the py launcher).
set "PYTHON_EXE="
set "PYTHON_ARGS="

if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_EXE=%ProgramFiles%\Python311\python.exe"
    goto :py_done
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :py_done
)
where py >nul 2>&1
if errorlevel 1 goto :try_plain_python
py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3.11"
    goto :py_done
)
set "PYTHON_EXE=py"
set "PYTHON_ARGS=-3"
goto :py_done

:try_plain_python
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=python"

:py_done
if "%PYTHON_EXE%"=="" (
    call :log "[ERROR] No Python interpreter found."
    exit /b 2
)
call :log "Using interpreter: %PYTHON_EXE% %PYTHON_ARGS%"

cd /d "%APP_DIR%" || (
    call :log "[ERROR] cd to %APP_DIR% failed."
    exit /b 3
)

if /i "%ACTION%"=="venv"   goto :do_venv
if /i "%ACTION%"=="initdb" goto :do_initdb

call :log "[ERROR] Unknown action: %ACTION%"
exit /b 4

:: -- Create venv + install dependencies --------------------------------------
:do_venv
if exist ".venv\Scripts\python.exe" (
    call :log ".venv already exists - reusing."
) else (
    call :log "Creating .venv ..."
    "%PYTHON_EXE%" %PYTHON_ARGS% -m venv ".venv" >>"%LOG_FILE%" 2>&1
    if errorlevel 1 (
        call :log "[ERROR] venv creation failed. Interpreter: %PYTHON_EXE% %PYTHON_ARGS%"
        exit /b 5
    )
)

:: -- Install dependencies OFFLINE from bundled wheels ------------------------
:: --no-index           : do not reach out to PyPI (no internet needed)
:: --find-links wheels  : look here for wheels and sdists
:: --no-build-isolation : do not try to install build deps from PyPI; the
::                        bundled setuptools/wheel are already installable
::                        from the same wheels/ folder if needed.
:: We prefer the bundled wheels/ folder; fall back to online install if it
:: is missing so dev builds without the wheel cache still work.
if exist "wheels\" (
    call :log "Installing requirements OFFLINE from bundled wheels/ ..."
    ".venv\Scripts\python.exe" -m pip install --no-index --find-links "wheels" --no-build-isolation -r "requirements.txt" --disable-pip-version-check >>"%LOG_FILE%" 2>&1
    if errorlevel 1 (
        call :log "[ERROR] Offline pip install failed. Check that wheels\ contains all required packages."
        exit /b 6
    )
) else (
    call :log "wheels\ not bundled - falling back to online pip install (requires internet)."
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check >>"%LOG_FILE%" 2>&1
    ".venv\Scripts\python.exe" -m pip install -r "requirements.txt" --disable-pip-version-check >>"%LOG_FILE%" 2>&1
    if errorlevel 1 (
        call :log "[ERROR] Online pip install failed - check internet connection."
        exit /b 6
    )
)
call :log "venv setup OK."
exit /b 0

:: -- Initialize the SQLite database ------------------------------------------
:do_initdb
if not exist ".venv\Scripts\python.exe" (
    call :log "[ERROR] .venv missing; run venv step first."
    exit /b 7
)
call :log "Running database init ..."
".venv\Scripts\python.exe" -c "from database import init_db; init_db()" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :log "[WARN] DB init returned non-zero; may already be initialized."
    exit /b 0
)
call :log "Database ready."
exit /b 0

:: -- Logging helper ----------------------------------------------------------
:log
echo [%date% %time%] %~1 >>"%LOG_FILE%"
exit /b 0
