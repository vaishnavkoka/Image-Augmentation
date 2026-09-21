@echo off
REM Image Mutation Tool - Windows launcher
REM
REM Starts the backend API and the web UI. The UI is advanced-index.html served
REM by frontend_server.py -- NOT `npm start`, which would serve the unused React
REM starter page under frontend\ on the same port. See DOCUMENTATION.md.
REM
REM Ports come from backend\.env (FLASK_PORT, FRONTEND_PORT) and default to
REM 5000 and 3000. Untested on Windows: run.sh on Linux or macOS is the
REM supported path, and doctor.py is the way to check this machine.

setlocal
cd /d "%~dp0.."

if not exist "backend\venv\Scripts\python.exe" (
    echo ERROR: no virtualenv found at backend\venv
    echo Create it first ^(Python 3.12 recommended, 3.10-3.13 supported^):
    echo     python -m venv backend\venv
    echo     backend\venv\Scripts\python -m pip install -r backend\requirements.txt
    pause
    exit /b 1
)

REM The backend reads backend\.env at import time -- notably MAGICK_HOME, which
REM selects the ImageMagick build. Without this file it binds to whatever is on
REM PATH, which may lack the png/tiff/webp/freetype delegates.
if not exist "backend\.env" (
    copy "backend\.env.example" "backend\.env" >nul
    echo Created backend\.env from the example.
)

echo Starting backend API ...
start "Image Mutation Tool - Backend" cmd /c "cd backend && ..\backend\venv\Scripts\python app.py"

timeout /t 4 /nobreak >nul

echo Starting web UI ...
start "Image Mutation Tool - Frontend" cmd /c "backend\venv\Scripts\python ui\frontend_server.py"

echo.
echo Each server prints its own address in its window.
echo With the defaults that is http://localhost:3000 for the UI.
echo.
echo Close the two server windows to stop.
echo.
pause
