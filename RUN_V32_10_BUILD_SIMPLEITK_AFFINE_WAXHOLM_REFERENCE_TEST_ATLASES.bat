@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo ============================================================
echo V32.10 Build SimpleITK Affine Waxholm Reference Test Atlases
echo ============================================================
echo Diagnostic test atlases only. Stable paxinos_watson_rat_40um is not overwritten.
echo.

set "PROJECT_ROOT=%~dp0"
if exist "%PROJECT_ROOT%src\experiments\v32_10_build_simpleitk_affine_waxholm_reference_test_atlases.py" goto have_project
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"

:have_project
set "SCRIPT=%PROJECT_ROOT%\src\experiments\v32_10_build_simpleitk_affine_waxholm_reference_test_atlases.py"
set "PACKAGE_SCRIPT=%~dp0src\experiments\v32_10_build_simpleitk_affine_waxholm_reference_test_atlases.py"
set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

echo Project root: %PROJECT_ROOT%
echo Script:       %SCRIPT%
echo Python:       %PYTHON%
echo.

if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project root does not exist: %PROJECT_ROOT%
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    if exist "%PACKAGE_SCRIPT%" (
        echo Copying V32.10 script into project...
        if not exist "%PROJECT_ROOT%\src\experiments" mkdir "%PROJECT_ROOT%\src\experiments"
        copy /Y "%PACKAGE_SCRIPT%" "%SCRIPT%" >nul
        if errorlevel 1 (
            echo ERROR: Could not copy V32.10 script into project.
            pause
            exit /b 1
        )
    ) else (
        echo ERROR: V32.10 script not found in project or package.
        pause
        exit /b 1
    )
)

if not exist "%PYTHON%" (
    echo ERROR: Project virtual environment Python not found:
    echo %PYTHON%
    echo Run your normal builder setup first.
    pause
    exit /b 1
)

"%PYTHON%" "%SCRIPT%"
set "ERR=%ERRORLEVEL%"

echo.
echo V32.10 finished with exit code %ERR%.
echo Reports should be in:
echo %PROJECT_ROOT%\reports\v32_10_simpleitk_affine_waxholm_reference_test_atlases
echo.
pause
exit /b %ERR%
