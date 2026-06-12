@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo ============================================================
echo V32.10 Install required deps into project venv and run
echo ============================================================
echo This modifies only the project Python environment by installing SimpleITK/tifffile if needed.
echo It does NOT modify the stable atlas. The run creates only separate diagnostic test atlases.
echo.
set /p CONFIRM=Install/check dependencies with pip in the project .venv? [y/N]: 
if /I not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

set "PROJECT_ROOT=%~dp0"
if exist "%PROJECT_ROOT%src\experiments\v32_10_build_simpleitk_affine_waxholm_reference_test_atlases.py" goto have_project
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"

:have_project
set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo ERROR: Project virtual environment Python not found:
    echo %PYTHON%
    pause
    exit /b 1
)

"%PYTHON%" -m pip install --upgrade pip
"%PYTHON%" -m pip install SimpleITK tifffile scipy nibabel matplotlib
if errorlevel 1 (
    echo ERROR: pip dependency install failed.
    pause
    exit /b 1
)

call "%~dp0RUN_V32_10_BUILD_SIMPLEITK_AFFINE_WAXHOLM_REFERENCE_TEST_ATLASES.bat"
exit /b %ERRORLEVEL%
