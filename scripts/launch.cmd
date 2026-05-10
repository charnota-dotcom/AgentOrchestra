@echo off
rem Start AgentOrchestra: opens two terminal windows, one for the
rem background service and one for the GUI.  Both auto-activate the
rem project's virtual environment.  Double-click from the desktop or
rem from the repo's `scripts/` folder.

setlocal
set REPO=%~dp0..

rem Sanity-check the venv exists so the user gets a real error
rem instead of two windows that flash and die.
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

start "AgentOrchestra Service" cmd /k ^
    "cd /d %REPO% && .venv\Scripts\activate.bat && python -m apps.service.main"

rem Give the service ~5 seconds to bind to localhost:8765 before the
rem GUI tries to connect.  The GUI doesn't crash if the service is
rem still starting — it just shows a transient RPC error toast.
timeout /t 5 /nobreak >nul

start "AgentOrchestra GUI" cmd /k ^
    "cd /d %REPO% && .venv\Scripts\activate.bat && python -m apps.gui.main"

endlocal
