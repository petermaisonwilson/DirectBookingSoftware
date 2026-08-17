@echo off
setlocal
cd /d "%~dp0"
title Direct Booking Software Online Build 013

echo.
echo ============================================================
echo   Direct Booking Software - Online Build 013
echo ============================================================
echo.
echo First run may take a minute while the web components install.
echo This build runs ONLY on this computer at http://127.0.0.1:8000
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3.11"
) else (
    set "PYTHON_CMD=python"
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
echo ERROR: Python 3.11 could not be started.
echo Build 013 needs Python 3.11 installed on this computer.
pause
goto :end

:install_error
echo.
echo ERROR: The required web components could not be installed.
echo Check that this PC has an internet connection and try again.
pause

:end
endlocal
