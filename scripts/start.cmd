@echo off
rem Verify CLI connections, then launch AgentOrchestra.
rem
rem Three steps:
rem
rem   [1/3] Probe `claude --version` (binary check)
rem         + a hard-time-bounded `claude -p "ping"` (auth check).
rem   [2/3] Same probes against `gemini`.
rem   [3/3] If at least one provider is OK, launch the GUI.
rem         Otherwise abort with fix-it instructions.
rem
rem Use this for first-of-day belt-and-braces confidence.  Otherwise
rem `launch.cmd` / `restart.cmd` are quicker.
rem
rem Implementation notes (this rewrite):
rem   * `setlocal enabledelayedexpansion` + `!errorlevel!` so nested
rem     ifs don't pick up parse-time stale values.
rem   * Each subprocess's exit code is captured into a NAMED variable
rem     immediately after the call, OUTSIDE any parenthesised block,
rem     because `%errorlevel%` inside `(...)` is unreliable in cmd.exe.
rem   * The headless `-p` probe is wrapped in PowerShell with a hard
rem     20-second timeout — the previous version had no timeout at all,
rem     so a hung claude / gemini would freeze the script forever and
rem     the operator would never see the Gemini check or the launch.
rem   * Both Claude AND Gemini blocks ALWAYS run regardless of what
rem     happened to the first.

setlocal enabledelayedexpansion
set REPO=%~dp0..

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [start] No virtual environment found at %REPO%\.venv
    echo [start] Run setup.cmd first to install AgentOrchestra.
    pause
    exit /b 1
)

set CLAUDE_OK=0
set GEMINI_OK=0

echo ================ Pre-flight ================
echo.

rem ====================================================================
rem [1/3] Claude Code
rem ====================================================================
echo [1/3] Claude Code CLI
where claude >nul 2>&1
set CLAUDE_PATH_RC=!errorlevel!
if not "!CLAUDE_PATH_RC!"=="0" (
    echo   claude: NOT FOUND on PATH.
    echo           Install with:  npm install -g @anthropic-ai/claude-code
    goto :gemini
)

claude --version
echo   probing 'claude -p ping' with a hard 20-second timeout...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath 'claude' -ArgumentList @('-p','respond with the single word OK') -PassThru -NoNewWindow -RedirectStandardOutput 'NUL' -RedirectStandardError 'NUL'; if ($p.WaitForExit(20000)) { exit $p.ExitCode } else { Stop-Process -Id $p.Id -Force; exit 124 } } catch { exit 99 }"
set CLAUDE_PROBE_RC=!errorlevel!
if "!CLAUDE_PROBE_RC!"=="0" (
    echo   claude: OK
    set CLAUDE_OK=1
) else if "!CLAUDE_PROBE_RC!"=="124" (
    echo   claude: probe TIMED OUT ^(^>20s^).  Probably hung on auth.
    echo           Run 'claude' interactively, type '/login', then re-run start.cmd.
) else (
    echo   claude: probe FAILED ^(exit !CLAUDE_PROBE_RC!^).
    echo           Likely 'Not logged in' — run 'claude' then '/login'.
)

:gemini
echo.

rem ====================================================================
rem [2/3] Gemini CLI
rem ====================================================================
echo [2/3] Gemini CLI
where gemini >nul 2>&1
set GEMINI_PATH_RC=!errorlevel!
if not "!GEMINI_PATH_RC!"=="0" (
    echo   gemini: NOT FOUND on PATH.
    echo           Install with:  npm install -g @google/gemini-cli
    goto :verdict
)

gemini --version
echo   probing 'gemini -p ping' with a hard 20-second timeout...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath 'gemini' -ArgumentList @('-p','respond with the single word OK') -PassThru -NoNewWindow -RedirectStandardOutput 'NUL' -RedirectStandardError 'NUL'; if ($p.WaitForExit(20000)) { exit $p.ExitCode } else { Stop-Process -Id $p.Id -Force; exit 124 } } catch { exit 99 }"
set GEMINI_PROBE_RC=!errorlevel!
if "!GEMINI_PROBE_RC!"=="0" (
    echo   gemini: OK
    set GEMINI_OK=1
) else if "!GEMINI_PROBE_RC!"=="124" (
    echo   gemini: probe TIMED OUT ^(^>20s^).  Probably hung on auth.
    echo           Run 'gemini' interactively to sign in, then re-run start.cmd.
) else (
    echo   gemini: probe FAILED ^(exit !GEMINI_PROBE_RC!^).
    echo           Likely an auth issue — run 'gemini' to sign in.
)

:verdict
echo.

rem ====================================================================
rem [3/3] Verdict + launch
rem ====================================================================
echo [3/3] Verdict
if "!CLAUDE_OK!"=="1" (
    echo   Claude:  ready
) else (
    echo   Claude:  unavailable
)
if "!GEMINI_OK!"=="1" (
    echo   Gemini:  ready
) else (
    echo   Gemini:  unavailable
)
echo.

if "!CLAUDE_OK!"=="0" if "!GEMINI_OK!"=="0" (
    echo Both providers failed.  Not launching the app.
    echo.
    echo Fixes:
    echo   * Run scripts\test-claude.cmd  ^(diagnoses Claude auth^)
    echo   * Run scripts\test-gemini.cmd  ^(diagnoses Gemini auth^)
    echo   * Then re-run scripts\start.cmd
    echo.
    pause
    exit /b 1
)

echo Launching AgentOrchestra...
start "AgentOrchestra" cmd /k "cd /d %REPO% && .venv\Scripts\activate.bat && python -m apps.gui.main"

echo.
echo Press any key to close this pre-flight window.
pause >nul
endlocal

