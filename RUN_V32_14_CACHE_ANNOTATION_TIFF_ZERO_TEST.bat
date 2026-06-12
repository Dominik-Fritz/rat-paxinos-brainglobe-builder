@echo off
setlocal enabledelayedexpansion
set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%\src\experiments\v32_14_annotation_tiff_display_proxy.py"

echo ============================================================
echo V32.14 Annotation TIFF display proxy - CACHE annotation.tiff zero test
echo ============================================================
echo Project root: %PROJECT_ROOT%
echo Script:       %SCRIPT%
echo Python:       %PY%
echo.

if not exist "%PY%" (
  echo ERROR: Python venv not found: %PY%
  echo Run your normal builder setup first.
  pause
  exit /b 1
)

if not exist "%PROJECT_ROOT%\src\experiments" mkdir "%PROJECT_ROOT%\src\experiments"
set "SRC_SCRIPT=%~dp0src\experiments\v32_14_annotation_tiff_display_proxy.py"

if not exist "%SRC_SCRIPT%" (
  if exist "%SCRIPT%" (
    echo Source script not found in package, but project script exists. Continuing with project script.
  ) else (
    echo ERROR: Source script not found in package: %SRC_SCRIPT%
    echo ERROR: Project script also missing: %SCRIPT%
    pause
    exit /b 1
  )
) else (
  if /I "%SRC_SCRIPT%"=="%SCRIPT%" (
    echo Script is already in the project location. Skipping self-copy.
  ) else (
    copy /Y "%SRC_SCRIPT%" "%SCRIPT%" >nul
    if errorlevel 1 (
      if exist "%SCRIPT%" (
        echo WARNING: Could not copy V32.14 script into project, but project script exists. Continuing.
      ) else (
        echo ERROR: Could not copy V32.14 script into project.
        pause
        exit /b 1
      )
    )
  )
)

"%PY%" "%SCRIPT%" --project-root "%PROJECT_ROOT%" --mode zero --target cache

echo.
echo Done. If this changed atlas display, fully restart Fiji/ABBA before testing.
pause
