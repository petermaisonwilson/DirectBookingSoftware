@echo off
setlocal
cd /d "%~dp0"

echo Direct Booking Web V1

echo.
set "PYTHON_CMD="

for %%C in ("py -3.13" "py -3.12" "py -3.11" "python") do (
  if not defined PYTHON_CMD (
    %%~C -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=%%~C"
  )
)

if not defined PYTHON_CMD (
  echo ERROR: Python 3.11 or newer was not found.
  echo Install Python, then run this file again.
  pause
  exit /b 1
)

if "%DIRECTBOOKING_LAUNCHER_TEST%"=="1" (
  echo Web V1 launcher Python detection passed: %PYTHON_CMD%
  exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing/updating required web packages...
.venv\Scripts\python.exe -m pip install -r requirements-online.txt
if errorlevel 1 goto :failed

echo.
echo Starting Direct Booking Web V1...
.venv\Scripts\python.exe run_online.py
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo Direct Booking Web V1 could not start. The error is shown above.
pause
exit /b 1
