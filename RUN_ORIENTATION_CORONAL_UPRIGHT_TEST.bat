@echo off
setlocal ENABLEDELAYEDEXPANSION

REM Orientation Coronal Upright Test
REM Creates a separate BrainGlobe atlas with axis order [AP, SI, LR].
REM It does NOT overwrite the stable paxinos_watson_rat_40um_v1.0 atlas.

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

echo.
echo ============================================================
echo  Paxinos orientation coronal-upright test
echo ============================================================
echo.
echo This creates:
echo   paxinos_watson_rat_40um_abba_coronal_upright_test
echo.
echo It does NOT overwrite:
echo   paxinos_watson_rat_40um
echo.

set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python venv not found:
    echo   %PYTHON_EXE%
    echo.
    echo Run your normal builder setup first, or adjust this BAT manually.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%PROJECT_ROOT%src\experiments\orientation_coronal_upright_test.py"
set "ERR=%ERRORLEVEL%"

echo.
if not "%ERR%"=="0" (
    echo FAILED with errorlevel %ERR%
    pause
    exit /b %ERR%
)

echo.
echo Done.
echo Restart Fiji/ABBA completely and open:
echo   paxinos_watson_rat_40um_abba_coronal_upright_test
echo.
echo Test buttons:
echo   Coronal    should be coronal, with dorsal/top up and left-right horizontal.
echo   Sagittal   should be sagittal.
echo   Horizontal should be horizontal.
echo.
pause
exit /b 0
