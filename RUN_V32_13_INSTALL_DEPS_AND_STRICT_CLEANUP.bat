@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%\src\maintenance\v32_13_label_only_display_cleanup.py"

echo ============================================================
echo V32.13 install deps + STRICT cleanup
echo ============================================================
if not exist "%PROJECT_ROOT%" (
  echo ERROR: Project root not found.
  pause
  exit /b 1
)
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install numpy nibabel tifffile
if not exist "%PROJECT_ROOT%\src\maintenance" mkdir "%PROJECT_ROOT%\src\maintenance"
copy /Y "%~dp0src\maintenance\v32_13_label_only_display_cleanup.py" "%SCRIPT%" >nul
"%PY%" "%SCRIPT%" --project-root "%PROJECT_ROOT%" --mode strict
pause
