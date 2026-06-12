@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo ============================================================
echo V32.12 LabelAtlas Rebaseline Cleanup - DRY RUN
echo ============================================================

echo This only reports what would be moved/removed. No changes.
echo.

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%src\v32_12_label_atlas_rebaseline_cleanup.py"

if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project root not found: %PROJECT_ROOT%
    echo Edit PROJECT_ROOT in this BAT if your project moved.
    pause
    exit /b 1
)

set "PY_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if exist "%PY_EXE%" (
    echo Python: %PY_EXE%
) else (
    echo Project venv Python not found. Trying py -3, then python...
    set "PY_EXE=py -3"
)

%PY_EXE% "%SCRIPT%" --project-root "%PROJECT_ROOT%" --dry-run
if errorlevel 1 (
    echo.
    echo ERROR: V32.12 dry-run failed.
    pause
    exit /b 1
)

echo.
echo DRY RUN DONE.
echo.
pause
