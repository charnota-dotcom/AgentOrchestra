@echo off
rem Pull the latest code and refresh dependencies.  Run this after
rem GitHub Desktop's Pull origin if you want to make sure your
rem virtual environment matches the new pyproject.toml.

setlocal
set REPO=%~dp0..

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [update] No virtual environment found.  Run setup.cmd first.
    pause
    exit /b 1
)

echo [update] git fetch + pull origin main ...
git -C "%REPO%" fetch origin || goto :err
git -C "%REPO%" pull --ff-only origin main || (
    echo [update] git pull failed.  Resolve the conflict in GitHub Desktop and re-run.
    pause
    exit /b 1
)

echo [update] pip install -e .[gui] --upgrade ...
call "%REPO%\.venv\Scripts\activate.bat"
pip install -e "%REPO%[gui]" --upgrade || goto :err

echo.
echo [update] Done.  Stop the app (stop.cmd) and start it again
echo [update] (launch.cmd or ops.cmd).
echo.
echo Press any key to close this window.
pause >nul
exit /b 0

:err
echo.
echo [update] Failed.  Read the error above, fix it, then re-run update.cmd.
echo.
pause
exit /b 1
