@echo off
setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0"

title Rat Paxinos BrainGlobe Builder - ABBA Visibility Patch V17.1

echo.
echo ============================================================
echo  V17.1 ABBA Visibility Patch
echo ============================================================
echo.
echo This patch makes ABBA include locally downloaded BrainGlobe atlases
echo in its atlas dropdown.
echo.
echo It creates backups of abba.py before editing.
echo.
echo Default behavior:
echo   - auto-discover ABBA-Python installations
echo   - patch ALL discovered installations
echo.
echo Optional:
echo   patch_abba_visibility.bat C:\Users\49152\abba-python-0.11.0
echo.

set "PY_EXE="
set "VENV_DIR=.venv"
set "REQ_FILE=requirements.txt"
set "ABBA_ROOT=%~1"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3.11 --version >nul 2>nul
    if !ERRORLEVEL!==0 (
        set "PY_EXE=py -3.11"
    ) else (
        py -3.12 --version >nul 2>nul
        if !ERRORLEVEL!==0 (
            set "PY_EXE=py -3.12"
        ) else (
            py -3 --version >nul 2>nul
            if !ERRORLEVEL!==0 set "PY_EXE=py -3"
        )
    )
)

if "%PY_EXE%"=="" (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 set "PY_EXE=python"
)

if "%PY_EXE%"=="" (
    echo Python not found. Run the main run_builder.bat first or install Python.
    pause
    popd
exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating local virtual environment...
    %PY_EXE% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        popd
exit /b 1
    )
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%REQ_FILE%"

if "%ABBA_ROOT%"=="" (
    echo.
    echo No ABBA path supplied. Auto-discovering and patching ALL ABBA installations...
    "%VENV_PY%" "src\v17_patch_abba_visibility.py" --all
) else (
    echo.
    echo Using ABBA root: %ABBA_ROOT%
    echo Patching this explicit ABBA installation...
    "%VENV_PY%" "src\v17_patch_abba_visibility.py" --abba-root "%ABBA_ROOT%" --all
)

if errorlevel 1 (
    echo.
    echo V17.1 ABBA patch failed. Review reports\v17_abba_visibility_patch_report.txt
    pause
    popd
exit /b 1
)

echo.
echo V17.1 ABBA patch done.
echo Restart ABBA completely, then check Open Atlas for:
echo paxinos_watson_rat_40um
echo.
pause
popd
exit /b 0
