@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo ============================================================
echo V32.12 LabelAtlas Rebaseline Cleanup - APPLY
echo ============================================================

echo This will remove/quarantine Paxinos test atlases from BrainGlobe
echo and restore the main paxinos_watson_rat_40um atlas from the
 echo project LabelAtlas candidate.
echo.
echo Stable target: paxinos_watson_rat_40um only
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

if not exist "%SCRIPT%" (
    echo ERROR: Cleanup script not found: %SCRIPT%
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

echo.
echo Running cleanup now. No raw data are deleted.
echo Test atlases are moved into backup/quarantine folders.
echo.
%PY_EXE% "%SCRIPT%" --project-root "%PROJECT_ROOT%" --apply --restore-main-cache
if errorlevel 1 (
    echo.
    echo ERROR: V32.12 cleanup failed.
    pause
    exit /b 1
)

echo.
echo DONE. Restart Fiji/ABBA completely before checking the atlas list.
echo.
pause
