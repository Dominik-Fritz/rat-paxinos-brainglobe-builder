@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo ============================================================
echo V32.15 Emergency restore LabelAtlas annotation display - CACHE ONLY
echo ============================================================

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
if not exist "%PROJECT_ROOT%" set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

set "PKG_DIR=%~dp0"
set "SRC_SCRIPT=%PKG_DIR%src\experiments\v32_15_emergency_restore_label_annotation.py"
set "DST_DIR=%PROJECT_ROOT%\src\experiments"
set "DST_SCRIPT=%DST_DIR%\v32_15_emergency_restore_label_annotation.py"

if not exist "%DST_DIR%" mkdir "%DST_DIR%"

if /I not "%SRC_SCRIPT%"=="%DST_SCRIPT%" (
    if exist "%SRC_SCRIPT%" copy /Y "%SRC_SCRIPT%" "%DST_SCRIPT%" >nul
)

if not exist "%DST_SCRIPT%" (
    echo ERROR: Could not find V32.15 script.
    echo Expected: %DST_SCRIPT%
    echo Also checked: %SRC_SCRIPT%
    pause
    exit /b 1
)

set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo Project root: %PROJECT_ROOT%
echo Script:       %DST_SCRIPT%
echo Python:       %PY%
echo.
echo IMPORTANT: Fiji/ABBA should be fully closed before this runs.
echo This restores annotation.tiff in the BrainGlobe cache to a full label volume.
echo It does NOT re-enable MRI/reference channels.
echo.
"%PY%" "%DST_SCRIPT%" --project-root "%PROJECT_ROOT%" --mode cache-auto

echo.
echo Done. Restart Fiji/ABBA completely and open paxinos_watson_rat_40um.
pause
