@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Rat Paxinos BrainGlobe Builder

echo.
echo ============================================================
echo  Rat Paxinos BrainGlobe Builder
echo ============================================================
echo.

set "PY_EXE="
set "VENV_DIR=.venv"
set "REQ_FILE=requirements.txt"
set "INSPECT_SCRIPT=src\inspect_inputs.py"

REM ------------------------------------------------------------
REM 1) Find Python
REM ------------------------------------------------------------
echo [1/5] Checking Python installation...

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
            if !ERRORLEVEL!==0 (
                set "PY_EXE=py -3"
            )
        )
    )
)

if "%PY_EXE%"=="" (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PY_EXE=python"
    )
)

if "%PY_EXE%"=="" (
    echo Python was not found.
    echo.
    choice /C YN /M "Install Python 3.11 via winget now?"
    if errorlevel 2 (
        echo Aborted. Install Python 3.11 manually and run this file again.
        pause
        exit /b 1
    )

    where winget >nul 2>nul
    if errorlevel 1 (
        echo winget was not found. Install Python 3.11 manually:
        echo https://www.python.org/downloads/
        pause
        exit /b 1
    )

    echo Installing Python 3.11 via winget...
    winget install --id Python.Python.3.11 -e --source winget

    echo.
    echo Python installation finished. Please close and restart this terminal/window,
    echo then run run_builder.bat again so PATH is refreshed.
    pause
    exit /b 0
)

%PY_EXE% --version
if errorlevel 1 (
    echo Python check failed.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 2) Create venv
REM ------------------------------------------------------------
echo.
echo [2/5] Checking local virtual environment...

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating local virtual environment: %VENV_DIR%
    %PY_EXE% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Existing virtual environment found.
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

REM ------------------------------------------------------------
REM 3) Install requirements
REM ------------------------------------------------------------
echo.
echo [3/5] Installing/updating Python requirements...

"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

"%VENV_PY%" -m pip install -r "%REQ_FILE%"
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 4) Run input inspection
REM ------------------------------------------------------------
echo.
echo [4/5] Running dataset inspection...

if not exist "%INSPECT_SCRIPT%" (
    echo Missing inspection script: %INSPECT_SCRIPT%
    pause
    exit /b 1
)

"%VENV_PY%" "%INSPECT_SCRIPT%"
if errorlevel 1 (
    echo.
    echo Inspection failed. Check the messages above and reports folder.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 5) Build status
REM ------------------------------------------------------------
echo.
echo [5/5] Build step

if exist "src\build_brainglobe_atlas.py" (
    echo The BrainGlobe build script exists, but automatic atlas generation is intentionally blocked
    echo until input geometry, reference volume, orientation, labels, and hierarchy are validated.
    echo.
    echo Next step: review reports\input_inspection_report.txt
) else (
    echo Build script not present yet. This first scaffold only validates inputs.
)

echo.
echo Done.
pause
endlocal
