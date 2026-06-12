@echo off
setlocal
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
echo Installing minimal dependencies into project venv...
"%PY%" -m pip install --upgrade nibabel tifffile numpy
if errorlevel 1 (
  echo ERROR: Dependency install failed.
  pause
  exit /b 1
)
call "%~dp0RUN_V32_14_CACHE_ANNOTATION_TIFF_BORDER_ONLY.bat"
