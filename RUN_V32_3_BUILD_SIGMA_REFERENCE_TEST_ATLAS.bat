@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%\src\v32_3_build_sigma_reference_test_atlas.py"

echo ========================================================================
echo V32.3 Build SIGMA anatomical reference TEST atlas
echo ========================================================================
echo.
echo This creates a separate test atlas and does NOT overwrite paxinos_watson_rat_40um.
echo.

if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project root not found: %PROJECT_ROOT%
    pause
    exit /b 1
)

cd /d "%PROJECT_ROOT%" || exit /b 1

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python venv not found: %PYTHON_EXE%
    echo Run the main builder once first so the project venv exists.
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo ERROR: Script not found: %SCRIPT%
    echo Copy src\v32_3_build_sigma_reference_test_atlas.py into the project src folder.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT%"
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
    echo FAILED with exit code %ERR%.
    echo Check reports\v32_3_sigma_reference_test\ for partial logs if created.
    pause
    exit /b %ERR%
)

echo Completed.
echo Open this atlas in ABBA for testing:
echo   paxinos_watson_rat_40um_sigma_reference_test
echo.
echo Reports:
echo   %PROJECT_ROOT%\reports\v32_3_sigma_reference_test
explorer "%PROJECT_ROOT%\reports\v32_3_sigma_reference_test"
pause
