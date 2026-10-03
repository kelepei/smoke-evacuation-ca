@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m experiments.platform_launcher
) else (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -m experiments.platform_launcher
  ) else (
    echo Python was not found. Install Python 3, then run this file again.
    pause
    exit /b 1
  )
)

set "LAUNCH_RESULT=%ERRORLEVEL%"
if not "%LAUNCH_RESULT%"=="0" (
  echo.
  echo Platform did not start. Read the error above, then press any key to close.
  pause
)

exit /b %LAUNCH_RESULT%
