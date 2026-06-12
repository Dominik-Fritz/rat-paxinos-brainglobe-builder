@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo ============================================================
echo V32.15 Aggressive rebuild annotation.tiff from annotation.nii.gz
echo ============================================================
echo.
echo WARNING: This rebuilds annotation.tiff in BOTH project and BrainGlobe cache.
echo Use only if the cache-only restore did not fix the V32.14 damage.
echo.

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
    pause
    exit /b 1
)

set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%DST_SCRIPT%" --project-root "%PROJECT_ROOT%" --mode aggressive-both-from-nifti
pause
