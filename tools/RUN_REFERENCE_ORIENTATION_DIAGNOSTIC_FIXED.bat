@echo off
setlocal EnableExtensions

set "PROJECT_ROOT=G:\rat-paxinos-brainglobe-builder"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "TOOLS_DIR=%PROJECT_ROOT%\tools"
set "SCRIPT=%TOOLS_DIR%\reference_orientation_diagnostic.py"
set "OUT=%PROJECT_ROOT%\reports\reference_orientation_diagnostic"
set "BAT_DIR=%~dp0"

if "%BAT_DIR:~-1%"=="\" set "BAT_DIR=%BAT_DIR:~0,-1%"

cls
echo ============================================================
echo Rat Paxinos reference/orientation diagnostic - FIXED runner
echo ============================================================
echo Project root: %PROJECT_ROOT%
echo Python:       %PY%
echo Target script:%SCRIPT%
echo Output:       %OUT%
echo BAT folder:   %BAT_DIR%
echo.

if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project root not found:
    echo %PROJECT_ROOT%
    pause
    exit /b 1
)

if not exist "%PY%" (
    echo ERROR: Python venv not found:
    echo %PY%
    pause
    exit /b 1
)

if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

REM Preferred: script is next to this BAT in .\tools\
if exist "%BAT_DIR%\tools\reference_orientation_diagnostic.py" (
    copy /Y "%BAT_DIR%\tools\reference_orientation_diagnostic.py" "%SCRIPT%" >nul
)

REM Fallback: script is in same folder as this BAT
if not exist "%SCRIPT%" if exist "%BAT_DIR%\reference_orientation_diagnostic.py" (
    copy /Y "%BAT_DIR%\reference_orientation_diagnostic.py" "%SCRIPT%" >nul
)

REM Fallback: user copied the extracted folder into project/tools/rat_atlas_diagnostic_tool_FIXED/tools
if not exist "%SCRIPT%" (
    for /R "%BAT_DIR%" %%F in (reference_orientation_diagnostic.py) do (
        copy /Y "%%F" "%SCRIPT%" >nul
        goto :found_script
    )
)
:found_script

if not exist "%SCRIPT%" (
    echo ERROR: Diagnostic script still not found after auto-copy.
    echo Expected or copied target:
    echo %SCRIPT%
    echo.
    echo This ZIP must contain:
    echo tools\reference_orientation_diagnostic.py
    echo.
    echo Folder listing of BAT location:
    dir "%BAT_DIR%" /s
    pause
    exit /b 1
)

echo Script ready at:
echo %SCRIPT%
echo.
echo Running diagnostic...
echo.

"%PY%" "%SCRIPT%"
set "ERR=%ERRORLEVEL%"

echo.
echo ============================================================
if "%ERR%"=="0" (
    echo Diagnostic finished successfully.
    echo Opening output folder...
    explorer "%OUT%"
) else (
    echo Diagnostic failed with exit code %ERR%.
    echo Check the console text above. Because naturally, one error was not enough.
)
echo ============================================================
pause
exit /b %ERR%
