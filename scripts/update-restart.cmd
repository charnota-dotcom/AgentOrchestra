@echo off
rem One-click "pull latest + restart AgentOrchestra".
rem
rem The common dev-loop workflow after merging a PR is:
rem   1. scripts\update.cmd   (git pull + pip install)
rem   2. scripts\restart.cmd  (kill stale service + relaunch GUI)
rem
rem This script does both atomically so the operator doesn't have
rem to chain them by hand.  Aborts on any update step that fails;
rem ALWAYS attempts the restart even if there was nothing to pull
rem (idempotent — if update is a no-op, restart still picks up any
rem freshly-merged code that was already pulled by other means).

setlocal
set REPO=%~dp0..

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [update-restart] No virtual environment found.  Run setup.cmd first.
    pause
    exit /b 1
)

echo ================ Update ================
echo.
echo [update-restart] git fetch + pull origin main ...
git -C "%REPO%" fetch origin || goto :err
git -C "%REPO%" pull --ff-only origin main || (
    echo [update-restart] git pull failed.  Resolve the conflict in GitHub Desktop and re-run.
    pause
    exit /b 1
)

echo [update-restart] pip install -e .[gui] --upgrade ...
call "%REPO%\.venv\Scripts\activate.bat"
pip install -e "%REPO%[gui]" --upgrade || goto :err

echo.
echo ================ Restart ================
echo.
echo [update-restart] Stopping any running AgentOrchestra...

rem Window-title kill (visible cmd hosts started by launch.cmd /
rem ops.cmd or by hand).
taskkill /FI "WINDOWTITLE eq AgentOrchestra*" /F /T >nul 2>&1
taskkill /FI "WINDOWTITLE eq AgentOrchestra Service*" /F /T >nul 2>&1

rem Port-listening kill (catches the supervisor-spawned service
rem which has CREATE_NO_WINDOW and so no window title).
for /f "tokens=5" %%P in (
    'netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"'
) do (
    echo   killing PID %%P (listening on :8765)
    taskkill /F /PID %%P >nul 2>&1
)

rem Belt-and-braces: kill orphan service processes whose command
rem line includes apps.service.main, regardless of port binding.
rem Same fix as restart.cmd / stop.cmd — catches the CREATE_NO_WINDOW
rem orphan that survived a partial earlier kill and would otherwise
rem keep answering on :8765 with stale RPC handlers.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine -like '*apps.service.main*' } | ForEach-Object { Write-Host \"  killing orphan service PID $($_.ProcessId)\"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem Tiny pause so the OS releases the port before the new GUI
rem probes it.
ping -n 2 127.0.0.1 >nul

echo [update-restart] Starting AgentOrchestra...
start "AgentOrchestra" cmd /k "cd /d %REPO% && .venv\Scripts\activate.bat && python -m apps.gui.main"

echo.
echo [update-restart] Done.  GUI is opening in a new window with the latest code.
echo.
echo Press any key to close this update-restart window.
pause >nul
exit /b 0

:err
echo.
echo [update-restart] Update failed.  Read the error above, fix it, then re-run.
echo.
pause
exit /b 1
