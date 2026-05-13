@echo off
set "SCRIPTS_DIR=%~dp0"

if "%1"=="--minimized" (
    shift
) else (
    start "AgentOrchestra Launcher" /min cmd /c "%~f0" --minimized %*
    exit /b
)

rem Start AgentOrchestra: opens just the GUI window. The service is
rem auto-spawned in the background (no console) by the GUI itself if
rem nothing is already listening on 127.0.0.1:8765 — see
rem apps/gui/service_supervisor.py.  Double-click from the desktop or
rem from the repo's `scripts/` folder.

setlocal
for %%I in ("%SCRIPTS_DIR%..") do set "REPO=%%~fI"

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [AgentOrchestra] No virtual environment found at %REPO%\.venv
    echo [AgentOrchestra] Run the one-time install first:
    echo     cd "%REPO%"
    echo     python -m venv .venv
    echo     .venv\Scripts\activate.bat
    echo     pip install -e ".[gui]"
    echo.
    pause
    exit /b 1
)

echo DEBUG: Found .venv at %REPO%\.venv
echo DEBUG: Launching AgentOrchestra...

rem cmd /k keeps the window open after python exits so any
rem traceback or error stays on screen for diagnosis.  Type `exit`
rem in that window when you're done reading.
start "AgentOrchestra" /min cmd /k ^
    "cd /d %REPO% && .venv\\Scripts\\activate.bat && python -m apps.gui.main"

endlocal
