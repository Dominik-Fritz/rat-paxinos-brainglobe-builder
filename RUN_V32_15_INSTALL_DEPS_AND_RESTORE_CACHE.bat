@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
if not exist "%PROJECT_ROOT%" set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install nibabel tifffile numpy
call "%~dp0RUN_V32_15_EMERGENCY_RESTORE_CACHE_LABEL_ANNOTATION.bat"
