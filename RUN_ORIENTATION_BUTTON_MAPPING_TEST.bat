@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Non-destructive ABBA button mapping test for Paxinos BrainGlobe atlas.
REM Creates a separate atlas name. Does NOT overwrite paxinos_watson_rat_40um_v1.0.

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

set "PY=%PROJECT_ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [WARN] Project venv not found: %PY%
    echo [INFO] Falling back to system python.
    set "PY=python"
)

echo ============================================================
echo ABBA button mapping test atlas
echo ============================================================
echo Project root: %PROJECT_ROOT%
echo Python: %PY%
echo.

"%PY%" "%PROJECT_ROOT%tools\create_abba_button_mapping_test_atlas.py"
set ERR=%ERRORLEVEL%

echo.
if not "%ERR%"=="0" (
    echo [ERROR] Test atlas creation failed with errorlevel %ERR%.
    pause
    exit /b %ERR%
)

echo [OK] Test atlas created.
echo.
echo Now restart Fiji/ABBA completely and open:
echo   paxinos_watson_rat_40um_abba_buttons_test
echo.
echo Expected mapping if this is correct:
echo   ABBA Coronal   - coronal
 echo   ABBA Sagittal  - sagittal
 echo   ABBA Horizontal - horizontal
 echo.
echo Also check sagittal: anterior should be left, posterior right.
echo.
pause
exit /b 0
