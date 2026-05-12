@echo off
rem AgentOrchestra environment diagnostic.
rem
rem Prints a one-page health report so you can copy/paste it when
rem something's wrong.  Checks the things that fail most often:
rem
rem * python on PATH and version
rem * the project's .venv exists and is healthy
rem * claude and gemini CLIs on PATH (subscription paths)
rem * port 8765 free / occupied
rem * the SQLite store + first-run sentinel
rem * the optional pyside6_annotator import

setlocal
set REPO=%~dp0..

echo ================ AgentOrchestra doctor ================
echo Repo:    %REPO%
echo Date:    %DATE% %TIME%
echo.

echo --- Python ---
where python && (python --version) || echo NOT FOUND on PATH
echo.

echo --- Virtual environment ---
if exist "%REPO%\.venv\Scripts\python.exe" (
    echo .venv: OK at %REPO%\.venv
    call "%REPO%\.venv\Scripts\activate.bat"
    pip --version
) else (
    echo .venv: MISSING — run scripts\setup.cmd
)
echo.

echo --- Subscription CLIs ---
rem `claude` and `gemini` are npm-installed .cmd shims; bare
rem invocation from a .cmd file is a tail-call and control would
rem never return — the rest of the doctor would be silently
rem skipped.  Always go through `call`.  Same fix as start.cmd.
where claude >nul 2>&1 && (call claude --version) || echo claude: NOT FOUND on PATH
where gemini >nul 2>&1 && (call gemini --version) || echo gemini: NOT FOUND on PATH
echo.

echo --- Service port (127.0.0.1:8765) ---
netstat -ano | findstr ":8765" >nul && (
    netstat -ano | findstr ":8765"
    echo Port 8765 is in use — service is probably running.
) || echo Port 8765 is free — service is not running.
echo.

rem Hit /healthz to see what RPC methods the running service has
rem actually registered.  This is the diagnostic that tells the
rem operator whether the service is on the latest code: a missing
rem method like `limits.check` or `blueprints.list` means the
rem running process was started before that registration landed
rem (annotation #7 root cause).  Run scripts\restart.cmd to swap
rem it out for a fresh process from the latest checkout.
echo --- Service /healthz ---
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/healthz' -TimeoutSec 3 -ErrorAction Stop; Write-Host \"  service replied; $($r.methods.Count) RPC methods registered.\"; foreach ($m in @('limits.check','blueprints.list','drones.list','skills.list')) { if ($m -in $r.methods) { Write-Host \"    [OK]      $m\" } else { Write-Host \"    [MISSING] $m  <-- service running stale code; run scripts\restart.cmd\" } } } catch { Write-Host \"  /healthz did not respond ($($_.Exception.Message))\" }"
echo.

echo --- Local data directory ---
set DATA=%USERPROFILE%\.local\share\agentorchestra
if exist "%DATA%" (
    echo %DATA% exists.
    if exist "%DATA%\agentorchestra.sqlite" (
        for %%I in ("%DATA%\agentorchestra.sqlite") do echo SQLite store: %%~zI bytes, modified %%~tI
    )
    if exist "%DATA%\first_run.done" echo First-run sentinel: present (wizard won't show again).
) else (
    echo %DATA% does not exist yet — will be created on first launch.
)
echo.

rem Tail the supervisor-spawned service log.  Captures stdout +
rem stderr of the auto-spawned `python -m apps.service.main` child
rem (see apps/gui/service_supervisor.py:service_log_path).  This
rem is the canonical place to see WHY a `drones.send` / `flows.*`
rem RPC failed — before the redirect, those tracebacks went to
rem /dev/null on Windows and the operator saw an empty "Send
rem failed" dialog with no body.
echo --- Recent service log (last 30 lines) ---
set SVCLOG=%USERPROFILE%\.local\share\agentorchestra\logs\service.log
if defined LOCALAPPDATA set SVCLOG=%LOCALAPPDATA%\agentorchestra\logs\service.log
if exist "%SVCLOG%" (
    echo path: %SVCLOG%
    echo.
    powershell -NoProfile -Command "Get-Content -Path '%SVCLOG%' -Tail 30 -ErrorAction SilentlyContinue"
) else (
    echo no service log found at %SVCLOG%
    echo ^(no auto-spawned service has run since the log redirect shipped,
    echo  or the operator started the service themselves outside the GUI^)
)
echo.

echo --- pyside6_annotator import ---
if exist "%REPO%\.venv\Scripts\python.exe" (
    "%REPO%\.venv\Scripts\python.exe" -c "import pyside6_annotator; print('OK', pyside6_annotator.__file__)" 2>nul || echo pyside6_annotator: NOT installed (the floating overlay won't show)
)
echo.

echo --- AgentOrchestra version ---
if exist "%REPO%\.venv\Scripts\python.exe" (
    "%REPO%\.venv\Scripts\python.exe" -c "from importlib.metadata import version; print(version('agentorchestra'))" 2>nul || echo agentorchestra: NOT installed in .venv
)
echo.

echo ================ End report ================
pause

