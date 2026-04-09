@echo off
REM run_engine_4456.bat
REM Starts the Engine on port 4456
 
echo ========================================
echo   Engine Server (Port 4456)
echo ========================================
echo.
 
REM Activate virtual environment
call venv\Scripts\activate.bat
 
REM Start backend server
echo Starting Engine on port 4456...
echo.
echo Press Ctrl+C to stop the server
echo.
 
.\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 4456
 
pause