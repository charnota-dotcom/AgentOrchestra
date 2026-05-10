@echo off
rem Open the AgentOrchestra Operator Panel — a tiny GUI window with
rem one button per command in this folder, plus a live output pane.
rem
rem Make this your "everyday" desktop shortcut if you want one
rem clickable thing that exposes every operation.

setlocal
set REPO=%~dp0..

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [ops] No virtual environment.  Run setup.cmd first.
    pause
    exit /b 1
)

rem cmd /k keeps the window open after python exits so any
rem traceback / error stays on screen for diagnosis.
start "AgentOrchestra Ops Panel" cmd /k ^
    "cd /d %REPO% && .venv\Scripts\activate.bat && python scripts\ops.py"

endlocal

