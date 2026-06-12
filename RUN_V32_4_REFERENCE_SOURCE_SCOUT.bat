@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "SCRIPT_REL=src\experiments\v32_4_reference_source_scout.py"
set "SCRIPT=%PROJECT_ROOT%\%SCRIPT_REL%"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

echo ============================================================
echo V32.4 Reference Source Scout
echo ============================================================
echo Project root: %PROJECT_ROOT%
echo Script:       %SCRIPT%
echo Python:       %PY%
echo.

if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project root not found: %PROJECT_ROOT%
    echo Edit PROJECT_ROOT in this BAT if your project lives elsewhere.
    pause
    exit /b 1
)

if not exist "%PY%" (
    echo ERROR: Python venv not found: %PY%
    echo Run the main builder once, or edit PY in this BAT.
    pause
    exit /b 1
)

if not exist "%PROJECT_ROOT%\src\experiments" mkdir "%PROJECT_ROOT%\src\experiments"
copy /Y "%~dp0src\experiments\v32_4_reference_source_scout.py" "%SCRIPT%" >nul
if errorlevel 1 (
    echo ERROR: Could not copy scout script into project.
    pause
    exit /b 1
)

echo Running scout. This does NOT modify any atlas files.
echo It only reads folders and writes reports.
echo.
"%PY%" "%SCRIPT%" --project-root "%PROJECT_ROOT%"
set "ERR=%ERRORLEVEL%"
echo.
echo Scout finished with exit code %ERR%.
echo.
if exist "%PROJECT_ROOT%\reports\v32_4_reference_source_scout\v32_4_reference_source_scout_summary.txt" (
    echo Opening summary report...
    notepad "%PROJECT_ROOT%\reports\v32_4_reference_source_scout\v32_4_reference_source_scout_summary.txt"
)
if exist "%PROJECT_ROOT%\reports\v32_4_reference_source_scout" (
    echo Opening report folder...
    start "" "%PROJECT_ROOT%\reports\v32_4_reference_source_scout"
)

echo.
echo Upload these files if you want the next patch based on real local sources:
echo   reports\v32_4_reference_source_scout\v32_4_reference_source_scout_summary.txt
echo   reports\v32_4_reference_source_scout\candidate_reference_sources.csv
echo   reports\v32_4_reference_source_scout\local_brainglobe_atlases.csv
echo   reports\v32_4_reference_source_scout\v32_4_reference_source_scout.json
echo.
pause
exit /b %ERR%
