@echo off
setlocal EnableExtensions
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"

set "PY_EXE=python"
if exist ".venv\Scripts\python.exe" set "PY_EXE=.venv\Scripts\python.exe"

if "%~1"=="" goto help
if /I "%~1"=="landmarks-status" goto run
if /I "%~1"=="landmarks-template" goto run
if /I "%~1"=="landmarks-affine" goto run
if /I "%~1"=="landmarks-warp" goto run
if /I "%~1"=="auto-affine" goto run
if /I "%~1"=="auto-warp" goto run
if /I "%~1"=="landmarks-accept" goto run
if /I "%~1"=="landmarks-reset" goto run
goto help

:run
"%PY_EXE%" src\v53_ch03_landmarker.py %*
exit /b %ERRORLEVEL%

:help
echo.
echo V53 Ch03 landmark-guided registration workflow
echo.
echo Usage:
echo   run_ch03_registration.bat landmarks-status
echo   run_ch03_registration.bat landmarks-template
echo   run_ch03_registration.bat landmarks-affine
echo   run_ch03_registration.bat landmarks-warp
echo   run_ch03_registration.bat auto-affine
echo   run_ch03_registration.bat auto-warp
echo   run_ch03_registration.bat landmarks-accept affine
echo   run_ch03_registration.bat landmarks-accept warp
echo   run_ch03_registration.bat landmarks-reset
echo.
echo This optional Ch03 workflow is local-only and does not call Docker, WSL, or SynthMorph.
exit /b 2
