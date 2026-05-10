@echo off
rem Restart AgentOrchestra — one click instead of stop.cmd then
rem launch.cmd.  Useful after `update.cmd` or after merging a PR
rem that changed service code: the running service has the OLD
rem code in memory and a fresh launch needs to spawn a new
rem process.

setlocal
set REPO=%~dp0..

echo [restart] Stopping any running AgentOrchestra...

rem Window-title kill (visible cmd hosts started by launch.cmd /
rem ops.cmd or by hand).
taskkill /FI "WINDOWTITLE eq AgentOrchestra*" /F /T >nul 2>&1
taskkill /FI "WINDOWTITLE eq AgentOrchestra Service*" /F /T >nul 2>&1

rem Port-listening kill (catches the supervisor-spawned service
rem which has CREATE_NO_WINDOW and so no window title).  See
rem stop.cmd for the same approach.
for /f "tokens=5" %%P in (
    'netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"'
) do (
    echo   killing PID %%P (listening on :8765)
    taskkill /F /PID %%P >nul 2>&1
)

rem Belt-and-braces: kill any python.exe / pythonw.exe whose
rem command line includes apps.service.main.  Catches orphan
rem services from older sessions that the window-title and
rem port-PID kills above can miss — e.g. a CREATE_NO_WINDOW
rem service whose port binding was closed but the python process
rem hasn't yet exited, or a service bound to a non-default port
rem because :8765 was busy when it spawned.  These orphans are
rem the documented root cause of "unknown method: limits.check"
rem after multiple restarts (annotation #7) — the GUI's
rem supervisor probes :8765, finds the orphan still answering,
rem and attaches to its old in-memory RPC table.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine -like '*apps.service.main*' } | ForEach-Object { Write-Host \"  killing orphan service PID $($_.ProcessId)\"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem Tiny pause so the OS releases the port before the new GUI
rem probes it.
ping -n 2 127.0.0.1 >nul

echo [restart] Starting AgentOrchestra...

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [restart] No virtual environment found.  Run setup.cmd first.
    pause
    exit /b 1
)

start "AgentOrchestra" cmd /k ^
    "cd /d %REPO% && .venv\Scripts\activate.bat && python -m apps.gui.main"

echo [restart] Done.  GUI is opening in a new window.
echo.
echo Press any key to close this restart window.
pause >nul
endlocal

