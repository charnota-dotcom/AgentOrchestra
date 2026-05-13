@echo off
set "SCRIPTS_DIR=%~dp0"

if "%1"=="--minimized" (
    shift
) else (
    start "AgentOrchestra Ops" /min cmd /c "%~f0" --minimized %*
    exit /b
)

rem Open the AgentOrchestra Operator Panel — a tiny GUI window with
rem one button per command in this folder, plus a live output pane.
rem
rem Make this your "everyday" desktop shortcut if you want one
rem clickable thing that exposes every operation.

setlocal
for %%I in ("%SCRIPTS_DIR%..") do set "REPO=%%~fI"

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [ops] No virtual environment.  Run setup.cmd first.
    pause
    exit /b 1
)

rem cmd /k keeps the window open after python exits so any
rem traceback / error stays on screen for diagnosis.
start "AgentOrchestra Ops Panel" /min cmd /k ^
    "cd /d %REPO% && .venv\\Scripts\\activate.bat && python scripts\\ops.py"

endlocal
