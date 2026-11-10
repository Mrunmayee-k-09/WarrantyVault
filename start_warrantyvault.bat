@echo off
setlocal
cd /d "%~dp0"
echo.
echo ==========================================
echo   WarrantyVault - local setup and launch
echo ==========================================
echo.
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher 'py' was not found. Install Python 3.10+ and enable Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :error
)
call ".venv\Scripts\activate.bat"
echo Installing or checking dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :error
pip install -r requirements.txt
if errorlevel 1 goto :error
if not exist ".env" copy ".env.example" ".env" >nul
echo Creating demo login and sample product records (safe to run more than once)...
flask --app run.py seed-demo
if errorlevel 1 goto :error
echo.
echo Starting the daily reminder worker in a minimized window...
if not exist "instance" mkdir "instance"
start "WarrantyVault Reminder Worker" /min cmd /c ""%CD%\.venv\Scripts\python.exe" "%CD%\run_scheduler.py" >> "%CD%\instance\reminder-scheduler.log" 2>&1"
echo Starting WarrantyVault at http://127.0.0.1:5000
echo Press Ctrl+C to stop the web server. Stop the reminder worker from Task Manager when closing the demo.
echo.
python run.py
goto :eof
:error
echo.
echo Setup failed. Check the error above, your Python installation, and internet connection.
pause
exit /b 1
