@echo off
setlocal
cd /d "%~dp0"
title Direct Booking Software Online Build 014

echo.
echo ============================================================
echo   Direct Booking Software - Online Build 014
echo ============================================================
echo.
echo First run may take a minute while the web components install.
echo This build runs ONLY on this computer at http://127.0.0.1:8000
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3.13"
    if not defined PYTHON_CMD (
        py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=py -3.12"
    )
    if not defined PYTHON_CMD (
        py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=py -3.11"
    )
)
if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=python"
    )
)
if not defined PYTHON_CMD goto :python_error
for /f "delims=" %%V in ('%PYTHON_CMD% --version 2^>^&1') do set "PYTHON_VERSION=%%V"
echo Using %PYTHON_VERSION%
if "%DIRECTBOOKING_LAUNCHER_TEST%"=="1" (
    echo Launcher Python detection test passed.
    exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
    echo Creating private Python environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :python_error
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :python_error
echo Checking required web components...
python -m pip install --disable-pip-version-check -q -r requirements-online.txt
if errorlevel 1 goto :install_error
echo.
echo Opening Direct Booking in your browser...
echo Keep this black window open while you are testing.
echo Close it, or press Ctrl+C, to stop the local reservation server.
echo.
python run_online.py
goto :end

:python_error
echo.
echo ERROR: I could not find a suitable Python installation.
echo Build 014 can use Python 3.11, 3.12 or 3.13.
echo.
where py >nul 2>nul
if not errorlevel 1 (
    echo Python versions Windows can currently see:
    py -0p
)
pause
goto :end

:install_error
echo.
echo ERROR: The required web components could not be installed.
echo Check that this PC has an internet connection and try again.
pause

:end
endlocal
