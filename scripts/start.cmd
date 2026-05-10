@echo off
rem Verify CLI connections, then launch AgentOrchestra.
rem
rem Three steps:
rem
rem 1. Probe ``claude``  with a tiny `claude -p "ping"` call.
rem 2. Probe ``gemini``  with a tiny `gemini -p "ping"` call.
rem 3. If at least one provider works, launch the GUI.
rem    If both fail, abort and tell the operator how to fix it.
rem
rem Use this when you want belt-and-braces confidence the chat tab
rem won't fail with a "Not logged in" 500 the moment you open it.
rem Otherwise launch.cmd / restart.cmd are quicker.

setlocal
set REPO=%~dp0..

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [start] No virtual environment found.  Run setup.cmd first.
    pause
    exit /b 1
)

set CLAUDE_OK=0
set GEMINI_OK=0

echo ================ Pre-flight ================
echo.

rem ----- Claude -----
echo --- Claude Code ---
where claude >nul 2>&1
if errorlevel 1 (
    echo   claude: NOT FOUND on PATH.
    echo   Install with:  npm install -g @anthropic-ai/claude-code
) else (
    claude --version
    echo   probing headless reply ^(timeout 30s^)...
    claude -p "respond with the single word OK" >nul 2>&1
    if errorlevel 1 (
        echo   claude: probe FAILED ^(likely "Not logged in" — run `claude` then `/login`^)
    ) else (
        echo   claude: OK
        set CLAUDE_OK=1
    )
)
echo.

rem ----- Gemini -----
echo --- Gemini CLI ---
where gemini >nul 2>&1
if errorlevel 1 (
    echo   gemini: NOT FOUND on PATH.
    echo   Install with:  npm install -g @google/gemini-cli
) else (
    gemini --version
    echo   probing headless reply ^(timeout 30s^)...
    gemini -p "respond with the single word OK" >nul 2>&1
    if errorlevel 1 (
        echo   gemini: probe FAILED ^(likely auth — run `gemini` to sign in^)
    ) else (
        echo   gemini: OK
        set GEMINI_OK=1
    )
)
echo.

rem ----- Verdict -----
echo ================ Verdict ================
if %CLAUDE_OK%==0 if %GEMINI_OK%==0 (
    echo Both providers failed.  Not launching the app.
    echo.
    echo Fixes:
    echo   * Install + log in:  scripts\test-claude.cmd  /  scripts\test-gemini.cmd
    echo   * Then re-run scripts\start.cmd
    echo.
    pause
    exit /b 1
)

if %CLAUDE_OK%==1 (echo   Claude:  ready) else (echo   Claude:  unavailable)
if %GEMINI_OK%==1 (echo   Gemini:  ready) else (echo   Gemini:  unavailable)
echo.

echo Launching AgentOrchestra...
start "AgentOrchestra" cmd /k ^
    "cd /d %REPO% && .venv\Scripts\activate.bat && python -m apps.gui.main"

echo.
echo Press any key to close this pre-flight window.
pause >nul
endlocal
