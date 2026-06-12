@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%\src\maintenance\v32_13_label_only_display_cleanup.py"

echo ============================================================
echo V32.13 restore latest backed-up display files
echo ============================================================
echo This restores reference/hemispheres/metadata files from the latest
 echo V32.13 backup folder where available.
echo.
pause
if not exist "%PROJECT_ROOT%" (
  echo ERROR: Project root not found.
  pause
  exit /b 1
)
if not exist "%PY%" set "PY=python"
if not exist "%PROJECT_ROOT%\src\maintenance" mkdir "%PROJECT_ROOT%\src\maintenance"
copy /Y "%~dp0src\maintenance\v32_13_label_only_display_cleanup.py" "%SCRIPT%" >nul
"%PY%" "%SCRIPT%" --project-root "%PROJECT_ROOT%" --restore-latest
pause
