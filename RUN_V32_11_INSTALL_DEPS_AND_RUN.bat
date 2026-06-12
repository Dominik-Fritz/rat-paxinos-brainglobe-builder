@echo off
setlocal EnableExtensions EnableDelayedExpansion
cls

echo ======================================================================
echo V32.11 Install dependencies and run Rank 01
echo ======================================================================

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "PIP=%PROJECT_ROOT%\.venv\Scripts\pip.exe"

if not exist "%PROJECT_ROOT%" (
  echo ERROR: Project root not found: %PROJECT_ROOT%
  pause
  exit /b 1
)
if not exist "%PY%" py -3 -m venv "%PROJECT_ROOT%\.venv"

"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install nibabel scipy matplotlib SimpleITK tifffile

call "%~dp0RUN_V32_11_BUILD_MULTIREG_AFFINE_RANK01.bat"
exit /b %ERRORLEVEL%
