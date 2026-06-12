@echo off
setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0"
set "PY_EXE="
set "VENV_DIR=.venv"
set "REQ_FILE=requirements.txt"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3.11 --version >nul 2>nul
    if !ERRORLEVEL!==0 (set "PY_EXE=py -3.11") else (
        py -3.12 --version >nul 2>nul
        if !ERRORLEVEL!==0 (set "PY_EXE=py -3.12") else (
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
    echo Python not found.
    pause
    popd
    exit /b 1
)
if not exist "%VENV_DIR%\Scripts\python.exe" (
    %PY_EXE% -m venv "%VENV_DIR%" || goto fail
)
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
"%VENV_PY%" -m pip install --upgrade pip || goto fail
"%VENV_PY%" -m pip install -r "%REQ_FILE%" || goto fail

echo.
echo ============================================================
echo  Reference / Orientation Diagnostic
echo ============================================================
echo.
"%VENV_PY%" "src\experiments\reference_orientation_diagnostic.py"
if errorlevel 1 goto fail
echo.
echo Done. Review reports output folder.
pause
popd
exit /b 0
:fail
echo.
echo Failed. Review console output and reports.
pause
popd
exit /b 1
