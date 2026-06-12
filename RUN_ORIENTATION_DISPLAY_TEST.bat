@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Orientation display test atlas creator
REM Creates a separate BrainGlobe atlas candidate with sagittal AP axis left-right.
REM Does NOT overwrite paxinos_watson_rat_40um_v1.0.

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo.
echo ============================================================
echo Paxinos orientation display test atlas
echo ============================================================
echo Project root: %PROJECT_ROOT%
echo Python: %PYTHON%
echo.

"%PYTHON%" "%PROJECT_ROOT%\tools\create_orientation_display_test_atlas.py" --project-root "%PROJECT_ROOT%" --install

echo.
echo Done. Check:
echo   %PROJECT_ROOT%\reports\orientation_display_test
echo.
pause
