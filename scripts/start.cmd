@echo off
set "SCRIPTS_DIR=%~dp0"

if "%1"=="--minimized" (
    shift
) else (
    start "AgentOrchestra Launcher" /min cmd /c "%~f0" --minimized %*
    exit /b
)

rem Verify CLI connections, then launch AgentOrchestra.
rem
rem  [1/3] Probe `claude --version` (binary check)
rem        + a hard-time-bounded `claude -p "ping"` (auth check).
rem  [2/3] Same probes against `gemini`.
rem  [3/3] If at least one provider is OK, launch the GUI.
rem        Otherwise abort with fix-it instructions.
rem
rem LF-safe rewrite (this file used to fail silently when stored
rem with LF line endings — cmd.exe drops nested parenthesised
rem `if/else if/else` blocks and `goto :label` jumps under LF).  We
rem now avoid those constructs entirely:
rem
rem   * NO `goto :label` jumps — sequential commands only.
rem   * NO nested parenthesised `if/else` — every branch is a
rem     single-line `if "x"=="y" command` so cmd parses each line
rem     independently and CRLF/LF stops mattering.
rem   * Each subprocess's exit code captured to a NAMED var
rem     immediately after the call.
rem   * The headless `-p` probe is wrapped in PowerShell with a
rem     hard 20-second timeout so a hung CLI can't freeze the
rem     script forever.  PowerShell-on-Windows gotchas the probe
rem     has to navigate:
rem       - `Start-Process` rejects `-RedirectStandardOutput` and
rem         `-RedirectStandardError` pointing to the same path; we
rem         use two distinct temp files cleaned up in `finally`.
rem       - `Start-Process -FilePath '<claude.cmd>'` itself is
rem         unreliable when redirecting stdout/stderr: with .cmd
rem         shim files some PS versions throw before launch even
rem         when the full path is given.  We work around it by
rem         shelling through `cmd.exe /c claude …` — cmd resolves
rem         PATHEXT and shim invocation natively, PowerShell just
rem         orchestrates the timeout.  (Symptoms of the wrong path
rem         here: catch block fires and the probe returns exit 99
rem         even when `claude -p …` works fine outside the wrapper.
rem         If you see exit 99 again, the catch now `Write-Host`s
rem         the actual exception message — read it.)
rem       - Gemini CLI refuses headless runs in an "untrusted"
rem         workspace; we set GEMINI_CLI_TRUST_WORKSPACE=true and
rem         pass `--skip-trust`, mirroring the gemini_cli.py
rem         provider's escape hatch.
rem   * `claude` and `gemini` are npm-installed `.cmd` shims, so
rem     calling them from a `.cmd` file MUST go through `call …` —
rem     a bare invocation is a tail-call and control never returns
rem     to this script (operator hit this: pre-flight printed
rem     `claude --version` then exited without ever probing Gemini
rem     or launching the GUI).
rem
rem Belt-and-braces — `.gitattributes` pins these files to CRLF on
rem checkout, but if a stale local copy survives, this script still
rem runs end-to-end.

setlocal enabledelayedexpansion
for %%I in ("%SCRIPTS_DIR%..") do set "REPO=%%~fI"

if not exist "%REPO%\.venv\Scripts\activate.bat" echo [start] No virtual environment found at %REPO%\.venv
if not exist "%REPO%\.venv\Scripts\activate.bat" echo [start] Run setup.cmd first to install AgentOrchestra.
if not exist "%REPO%\.venv\Scripts\activate.bat" pause
if not exist "%REPO%\.venv\Scripts\activate.bat" exit /b 1

set CLAUDE_OK=0
set GEMINI_OK=0
set CODEX_OK=0
set CLAUDE_PRESENT=0
set GEMINI_PRESENT=0
set CODEX_PRESENT=0

echo ================ Pre-flight ================
echo.

rem ====================================================================
rem [1/3] Claude Code
rem ====================================================================
echo [1/3] Claude Code CLI
where claude >nul 2>&1
set CLAUDE_PATH_RC=!errorlevel!
if "!CLAUDE_PATH_RC!"=="0" set CLAUDE_PRESENT=1

if "!CLAUDE_PRESENT!"=="0" echo   claude: NOT FOUND on PATH.
if "!CLAUDE_PRESENT!"=="0" echo           Install with:  npm install -g @anthropic-ai/claude-code

if "!CLAUDE_PRESENT!"=="1" call claude --version
if "!CLAUDE_PRESENT!"=="1" echo   probing 'claude -p ping' with a hard 20-second timeout...
if "!CLAUDE_PRESENT!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -Command "$o=[IO.Path]::GetTempFileName(); $e=[IO.Path]::GetTempFileName(); try { $p = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','claude','-p','respond with the single word OK') -PassThru -NoNewWindow -RedirectStandardOutput $o -RedirectStandardError $e; if ($p.WaitForExit(20000)) { exit $p.ExitCode } else { Stop-Process -Id $p.Id -Force; exit 124 } } catch { Write-Host ('  [probe-exc] ' + $_.Exception.Message); exit 99 } finally { Remove-Item $o,$e -ErrorAction SilentlyContinue }"
if "!CLAUDE_PRESENT!"=="1" set CLAUDE_PROBE_RC=!errorlevel!
if "!CLAUDE_PRESENT!"=="0" set CLAUDE_PROBE_RC=-1

if "!CLAUDE_PROBE_RC!"=="0" set CLAUDE_OK=1
if "!CLAUDE_PROBE_RC!"=="0" echo   claude: OK
if "!CLAUDE_PROBE_RC!"=="124" echo   claude: probe TIMED OUT ^(^>20s^).  Probably hung on auth.
if "!CLAUDE_PROBE_RC!"=="124" echo           Run 'claude' interactively, type '/login', then re-run start.cmd.
if "!CLAUDE_PROBE_RC!"=="99" echo   claude: PowerShell wrapper threw an exception ^(exit 99^).
if "!CLAUDE_PROBE_RC!"=="99" echo           Run scripts\test-claude.cmd for a direct probe; if that succeeds the issue is in the start.cmd wrapper, not your auth.
if "!CLAUDE_PRESENT!"=="1" if not "!CLAUDE_PROBE_RC!"=="0" if not "!CLAUDE_PROBE_RC!"=="124" if not "!CLAUDE_PROBE_RC!"=="99" echo   claude: probe FAILED ^(exit !CLAUDE_PROBE_RC!^).
if "!CLAUDE_PRESENT!"=="1" if not "!CLAUDE_PROBE_RC!"=="0" if not "!CLAUDE_PROBE_RC!"=="124" if not "!CLAUDE_PROBE_RC!"=="99" echo           Likely 'Not logged in' — run 'claude' then '/login'.

echo.

rem ====================================================================
rem [2/3] Gemini CLI
rem ====================================================================
echo [2/3] Gemini CLI
where gemini >nul 2>&1
set GEMINI_PATH_RC=!errorlevel!
if "!GEMINI_PATH_RC!"=="0" set GEMINI_PRESENT=1

if "!GEMINI_PRESENT!"=="0" echo   gemini: NOT FOUND on PATH.
if "!GEMINI_PRESENT!"=="0" echo           Install with:  npm install -g @google/gemini-cli

if "!GEMINI_PRESENT!"=="1" call gemini --version
if "!GEMINI_PRESENT!"=="1" echo   probing 'gemini -p ping' with a hard 20-second timeout...
if "!GEMINI_PRESENT!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -Command "$o=[IO.Path]::GetTempFileName(); $e=[IO.Path]::GetTempFileName(); $env:GEMINI_CLI_TRUST_WORKSPACE='true'; try { $p = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','gemini','-p','respond with the single word OK','--skip-trust') -PassThru -NoNewWindow -RedirectStandardOutput $o -RedirectStandardError $e; if ($p.WaitForExit(20000)) { exit $p.ExitCode } else { Stop-Process -Id $p.Id -Force; exit 124 } } catch { Write-Host ('  [probe-exc] ' + $_.Exception.Message); exit 99 } finally { Remove-Item $o,$e -ErrorAction SilentlyContinue }"
if "!GEMINI_PRESENT!"=="1" set GEMINI_PROBE_RC=!errorlevel!
if "!GEMINI_PRESENT!"=="0" set GEMINI_PROBE_RC=-1

if "!GEMINI_PROBE_RC!"=="0" set GEMINI_OK=1
if "!GEMINI_PROBE_RC!"=="0" echo   gemini: OK
if "!GEMINI_PROBE_RC!"=="124" echo   gemini: probe TIMED OUT ^(^>20s^).  Probably hung on auth.
if "!GEMINI_PROBE_RC!"=="124" echo           Run 'gemini' interactively to sign in, then re-run start.cmd.
if "!GEMINI_PROBE_RC!"=="99" echo   gemini: PowerShell wrapper threw an exception ^(exit 99^).
if "!GEMINI_PROBE_RC!"=="99" echo           Run scripts\test-gemini.cmd for a direct probe; if that succeeds the issue is in the start.cmd wrapper, not your auth.
if "!GEMINI_PRESENT!"=="1" if not "!GEMINI_PROBE_RC!"=="0" if not "!GEMINI_PROBE_RC!"=="124" if not "!GEMINI_PROBE_RC!"=="99" echo   gemini: probe FAILED ^(exit !GEMINI_PROBE_RC!^).
if "!GEMINI_PRESENT!"=="1" if not "!GEMINI_PROBE_RC!"=="0" if not "!GEMINI_PROBE_RC!"=="124" if not "!GEMINI_PROBE_RC!"=="99" echo           Likely an auth issue — run 'gemini' to sign in.

echo.

rem ====================================================================
rem [3/3] Codex CLI
rem ====================================================================
echo [3/3] Codex CLI
where codex >nul 2>&1
set CODEX_PATH_RC=!errorlevel!
if "!CODEX_PATH_RC!"=="0" set CODEX_PRESENT=1

if "!CODEX_PRESENT!"=="0" echo   codex: NOT FOUND on PATH.
if "!CODEX_PRESENT!"=="0" echo           Install with:  npm install -g @openai/codex

if "!CODEX_PRESENT!"=="1" call codex --version
if "!CODEX_PRESENT!"=="1" echo   probing 'codex exec' with a hard 20-second timeout...
if "!CODEX_PRESENT!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -Command "$o=[IO.Path]::GetTempFileName(); $e=[IO.Path]::GetTempFileName(); try { $p = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','codex','exec','respond with the single word OK') -PassThru -NoNewWindow -RedirectStandardOutput $o -RedirectStandardError $e; if ($p.WaitForExit(20000)) { exit $p.ExitCode } else { Stop-Process -Id $p.Id -Force; exit 124 } } catch { Write-Host ('  [probe-exc] ' + $_.Exception.Message); exit 99 } finally { Remove-Item $o,$e -ErrorAction SilentlyContinue }"
if "!CODEX_PRESENT!"=="1" set CODEX_PROBE_RC=!errorlevel!
if "!CODEX_PRESENT!"=="0" set CODEX_PROBE_RC=-1

if "!CODEX_PROBE_RC!"=="0" set CODEX_OK=1
if "!CODEX_PROBE_RC!"=="0" echo   codex: OK
if "!CODEX_PROBE_RC!"=="124" echo   codex: probe TIMED OUT ^(^>20s^).  Probably hung on auth.
if "!CODEX_PROBE_RC!"=="124" echo           Run 'codex' interactively to sign in, then re-run start.cmd.
if "!CODEX_PROBE_RC!"=="99" echo   codex: PowerShell wrapper threw an exception ^(exit 99^).
if "!CODEX_PROBE_RC!"=="99" echo           Run a direct 'codex exec' probe; if that succeeds the issue is in the start.cmd wrapper, not your auth.
if "!CODEX_PRESENT!"=="1" if not "!CODEX_PROBE_RC!"=="0" if not "!CODEX_PROBE_RC!"=="124" if not "!CODEX_PROBE_RC!"=="99" echo   codex: probe FAILED ^(exit !CODEX_PROBE_RC!^).
if "!CODEX_PRESENT!"=="1" if not "!CODEX_PROBE_RC!"=="0" if not "!CODEX_PROBE_RC!"=="124" if not "!CODEX_PROBE_RC!"=="99" echo           Likely an auth issue - run 'codex' to sign in.

echo.

rem ====================================================================
rem [4/4] Verdict + launch
rem ====================================================================
echo [4/4] Verdict
if "!CLAUDE_OK!"=="1" echo   Claude:  ready
if not "!CLAUDE_OK!"=="1" echo   Claude:  unavailable
if "!GEMINI_OK!"=="1" echo   Gemini:  ready
if not "!GEMINI_OK!"=="1" echo   Gemini:  unavailable
if "!CODEX_OK!"=="1" echo   Codex:   ready
if not "!CODEX_OK!"=="1" echo   Codex:   unavailable
echo.

set ALL_FAILED=0
if "!CLAUDE_OK!"=="0" if "!GEMINI_OK!"=="0" if "!CODEX_OK!"=="0" set ALL_FAILED=1

if "!ALL_FAILED!"=="1" echo All providers failed.  Not launching the app.
if "!ALL_FAILED!"=="1" echo.
if "!ALL_FAILED!"=="1" echo Fixes:
if "!ALL_FAILED!"=="1" echo   * Run scripts\test-claude.cmd  ^(diagnoses Claude auth^)
if "!ALL_FAILED!"=="1" echo   * Run scripts\test-gemini.cmd  ^(diagnoses Gemini auth^)
if "!ALL_FAILED!"=="1" echo   * Run 'codex' interactively, then retry the launcher
if "!ALL_FAILED!"=="1" echo   * Then re-run scripts\start.cmd
if "!ALL_FAILED!"=="1" echo.
if "!ALL_FAILED!"=="1" pause
if "!ALL_FAILED!"=="1" exit /b 1

echo Launching AgentOrchestra...
start "AgentOrchestra" /min cmd /k "cd /d %REPO% && .venv\\Scripts\\activate.bat && python -m apps.gui.main"

echo.
echo Press any key to close this pre-flight window.
pause >nul
endlocal
