@echo off
setlocal EnableExtensions EnableDelayedExpansion
cls

echo ======================================================================
echo V32.11 FULL-RES Rank 01 experiment - heavy mode
echo ======================================================================
echo WARNING: This can be slow and memory-heavy. Close Fiji/ABBA first.
echo If Windows starts sounding like a small jet engine, that is not a feature.
echo.
pause

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "SCRIPT_REL=src\experiments\v32_11_build_multires_simpleitk_affine_waxholm_reference_test.py"
set "PROJECT_SCRIPT=%PROJECT_ROOT%\%SCRIPT_REL%"
set "PACKAGE_SCRIPT=%~dp0%SCRIPT_REL%"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%PROJECT_ROOT%\src\experiments" mkdir "%PROJECT_ROOT%\src\experiments"
if not exist "%PROJECT_SCRIPT%" copy /Y "%PACKAGE_SCRIPT%" "%PROJECT_SCRIPT%" >nul
if not exist "%PY%" py -3 -m venv "%PROJECT_ROOT%\.venv"

set "V32_11_RANKS=1"
set "V32_11_REGISTRATION_FACTOR=1"
"%PY%" "%PROJECT_SCRIPT%"
set ERR=%ERRORLEVEL%
pause
exit /b %ERR%
