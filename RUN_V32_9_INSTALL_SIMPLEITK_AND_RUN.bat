@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo ============================================================
echo V32.9 Install SimpleITK into project venv and run diagnostic
echo ============================================================

echo This modifies only the project Python environment by installing SimpleITK.
echo It does NOT modify atlas data and does NOT install/promote an atlas.
echo.
set /p CONFIRM=Install SimpleITK with pip in the project .venv? [y/N]: 
if /I not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

set "PROJECT_ROOT=%~dp0"
if exist "%PROJECT_ROOT%src\experiments\v32_9_registration_backend_prep.py" goto have_project
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"

:have_project
set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%\src\experiments\v32_9_registration_backend_prep.py"
set "PACKAGE_SCRIPT=%~dp0src\experiments\v32_9_registration_backend_prep.py"

echo Project root: %PROJECT_ROOT%
echo Python:       %PYTHON%
echo.

if not exist "%PYTHON%" (
    echo ERROR: Project virtual environment Python not found:
    echo %PYTHON%
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    if exist "%PACKAGE_SCRIPT%" (
        echo Copying V32.9 script into project...
        if not exist "%PROJECT_ROOT%\src\experiments" mkdir "%PROJECT_ROOT%\src\experiments"
        copy /Y "%PACKAGE_SCRIPT%" "%SCRIPT%" >nul
        if errorlevel 1 (
            echo ERROR: Could not copy V32.9 script into project.
            pause
            exit /b 1
        )
    )
)

echo Installing/updating pip tooling...
"%PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo WARNING: pip tooling update failed. Continuing with SimpleITK install attempt.
)

echo Installing SimpleITK...
"%PYTHON%" -m pip install SimpleITK
if errorlevel 1 (
    echo ERROR: SimpleITK install failed. Check internet/proxy/build environment.
    pause
    exit /b 1
)

echo Running V32.9 diagnostic...
"%PYTHON%" "%SCRIPT%"
set "ERR=%ERRORLEVEL%"

echo.
echo V32.9 finished with exit code %ERR%.
echo Reports should be in:
echo %PROJECT_ROOT%\reports\v32_9_registration_backend_prep

echo.
pause
exit /b %ERR%
