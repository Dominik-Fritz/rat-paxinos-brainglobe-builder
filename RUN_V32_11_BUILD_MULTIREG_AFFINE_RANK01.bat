@echo off
setlocal EnableExtensions EnableDelayedExpansion
cls

echo ======================================================================
echo V32.11 Multi-resolution SimpleITK Affine Waxholm Reference Test - Rank 01
echo ======================================================================

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "SCRIPT_REL=src\experiments\v32_11_build_multires_simpleitk_affine_waxholm_reference_test.py"
set "PROJECT_SCRIPT=%PROJECT_ROOT%\%SCRIPT_REL%"
set "PACKAGE_SCRIPT=%~dp0%SCRIPT_REL%"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%PROJECT_ROOT%" (
  echo ERROR: Project root not found: %PROJECT_ROOT%
  pause
  exit /b 1
)

if not exist "%PROJECT_ROOT%\src\experiments" mkdir "%PROJECT_ROOT%\src\experiments"

if exist "%PROJECT_SCRIPT%" (
  echo Using project script: %PROJECT_SCRIPT%
) else (
  if exist "%PACKAGE_SCRIPT%" (
    echo Copying script into project...
    copy /Y "%PACKAGE_SCRIPT%" "%PROJECT_SCRIPT%" >nul
  ) else (
    echo ERROR: Missing script in package and project.
    echo Package script expected: %PACKAGE_SCRIPT%
    pause
    exit /b 1
  )
)

if not exist "%PY%" (
  echo Creating project virtual environment...
  py -3 -m venv "%PROJECT_ROOT%\.venv"
)

set "V32_11_RANKS=1"
set "V32_11_REGISTRATION_FACTOR=2"

echo.
echo Project root: %PROJECT_ROOT%
echo Script:       %PROJECT_SCRIPT%
echo Python:       %PY%
echo Ranks:        %V32_11_RANKS%
echo Factor:       %V32_11_REGISTRATION_FACTOR%
echo.
"%PY%" "%PROJECT_SCRIPT%"
set ERR=%ERRORLEVEL%
echo.
if not "%ERR%"=="0" (
  echo V32.11 failed. If dependencies are missing, run RUN_V32_11_INSTALL_DEPS_AND_RUN.bat
) else (
  echo V32.11 finished. Upload the summary/report/previews from reports\v32_11_multires_simpleitk_affine_waxholm_reference_test
)
pause
exit /b %ERR%
