@echo off
rem Smoke-test the local Gemini CLI.  Same three checks as
rem test-claude.cmd, applied to the gemini binary.
rem
rem If the headless call fails for auth reasons, run `gemini` once
rem interactively to log in.  Credentials persist after that.
rem
rem `gemini` is an npm-installed .cmd shim; bare invocation from a
rem .cmd file is a tail-call and control never returns.  Every
rem invocation below is prefixed with `call` for that reason.

echo ================ Gemini CLI test ================
echo.

where gemini >nul 2>&1
if errorlevel 1 (
    echo gemini: NOT FOUND on PATH.
    echo.
    echo Install with:
    echo     npm install -g @google/gemini-cli
    echo Then run this test again.
    echo.
    echo Press any key to close.
    pause >nul
    exit /b 1
)

echo --- gemini --version ---
call gemini --version
echo.

echo --- gemini -p "say hi in 5 words" ---
echo (waiting up to 30s for a reply...)
echo.
rem `--skip-trust` + GEMINI_CLI_TRUST_WORKSPACE: Gemini CLI refuses
rem headless runs in any folder it doesn't consider trusted.  Both
rem the env var and the flag are needed because different CLI
rem versions honour different bypasses.  Mirrors the production
rem gemini_cli.py provider's escape hatch.
set GEMINI_CLI_TRUST_WORKSPACE=true
call gemini -p "say hi in 5 words" --skip-trust
set GEMINI_EXIT=%ERRORLEVEL%
echo.

if %GEMINI_EXIT%==0 (
    echo Gemini CLI: OK — auth works, headless mode replied.
) else (
    echo Gemini CLI: returned exit %GEMINI_EXIT%.
    echo If the error mentioned auth or login, run:
    echo     gemini
    echo and complete the sign-in.  Credentials persist.  Then
    echo re-run this test.
)

echo.
echo ================ End test ================
echo.
echo Press any key to close.
pause >nul

