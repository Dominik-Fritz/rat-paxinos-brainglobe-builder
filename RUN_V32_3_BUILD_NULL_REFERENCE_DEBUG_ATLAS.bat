@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%\src\v32_3_build_null_reference_debug_atlas.py"

echo ========================================================================
echo V32.3 Build NULL reference debug atlas
echo ========================================================================
echo.
echo This creates a separate debug atlas with reference.tiff set to zero.
echo It is only for diagnosing what ABBA still displays when the reference is empty.
echo.

if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project root not found: %PROJECT_ROOT%
    pause
    exit /b 1
)

cd /d "%PROJECT_ROOT%" || exit /b 1

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python venv not found: %PYTHON_EXE%
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo ERROR: Script not found: %SCRIPT%"
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT%"
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
    echo FAILED with exit code %ERR%.
    pause
    exit /b %ERR%
)

echo Completed.
echo Open this atlas in ABBA for testing:
echo   paxinos_watson_rat_40um_null_reference_debug
explorer "%PROJECT_ROOT%\reports\v32_3_null_reference_debug"
pause
