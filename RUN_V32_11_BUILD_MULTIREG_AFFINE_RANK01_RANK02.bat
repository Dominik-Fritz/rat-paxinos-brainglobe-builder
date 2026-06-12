@echo off
setlocal EnableExtensions EnableDelayedExpansion
cls

echo ======================================================================
echo V32.11 Multi-resolution SimpleITK Affine Waxholm Reference Test - Rank 01 + 02
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
if not exist "%PROJECT_SCRIPT%" copy /Y "%PACKAGE_SCRIPT%" "%PROJECT_SCRIPT%" >nul
if not exist "%PY%" py -3 -m venv "%PROJECT_ROOT%\.venv"

set "V32_11_RANKS=1,2"
set "V32_11_REGISTRATION_FACTOR=2"

echo Running Rank 01 and Rank 02. This takes longer, because apparently one rat brain was not enough.
"%PY%" "%PROJECT_SCRIPT%"
set ERR=%ERRORLEVEL%
pause
exit /b %ERR%
