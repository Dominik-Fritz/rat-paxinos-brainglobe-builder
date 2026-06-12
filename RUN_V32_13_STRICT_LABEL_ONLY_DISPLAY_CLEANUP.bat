@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%\src\maintenance\v32_13_label_only_display_cleanup.py"

echo ============================================================
echo V32.13 STRICT LabelAtlas display cleanup
echo ============================================================
echo This will backup files, quarantine test atlases, zero reference,
echo and zero hemispheres.tiff helper display in the active stable atlas.
echo Close Fiji/ABBA before continuing.
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
"%PY%" "%SCRIPT%" --project-root "%PROJECT_ROOT%" --mode strict
pause
