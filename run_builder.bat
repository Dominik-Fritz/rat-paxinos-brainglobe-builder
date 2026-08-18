@echo off
set "PYTHONDONTWRITEBYTECODE=1"
setlocal EnableExtensions EnableDelayedExpansion

title Rat Paxinos BrainGlobe Builder
cd /d "%~dp0"
set "BUILDER_ROOT=%~dp0."

echo.
echo ============================================================
echo  Rat Paxinos BrainGlobe Builder
echo  LabelAtlas-only release candidate
echo ============================================================
echo.
echo This runner builds/installs the Paxinos-Watson rat LabelAtlas.
echo It does not build Waxholm/SIGMA/NeuroRat/MRI reference channels.
echo.
echo Required ABBA display after build:
echo   reference (Ch. 0)                         = ON
echo   soft_region_fill_reference (Ch. 1)         = optional
echo   distance_to_2d_outline_reference (Ch. 2)   = optional
echo   native borders display source              = hidden by V44
echo.

set "PY_EXE="
set "VENV_DIR=.venv"
set "REQ_FILE=requirements.txt"
set "PATCH_ABBA=ASK"

for %%A in (%*) do (
    if /I "%%~A"=="--patch-abba" set "PATCH_ABBA=YES"
    if /I "%%~A"=="--no-patch-abba" set "PATCH_ABBA=NO"
)

echo [1/30] Checking Python installation...
call :detect_python

if "%PY_EXE%"=="" (
    echo Suitable Python 3.11/3.12 was not found.
    where winget >nul 2>nul
    if errorlevel 1 (
        echo winget was not found. Install Python 3.11 or 3.12 manually and run again.
        pause
        exit /b 1
    )

    echo Installing Python 3.12 via winget...
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo winget Python installation failed. Install Python 3.11 or 3.12 manually and run again.
        pause
        exit /b 1
    )

    echo Re-checking Python after winget install...
    call :detect_python
)

if "%PY_EXE%"=="" (
    echo Python was installed but is not visible in this terminal yet.
    echo Close this terminal, open a new one, and run run_builder.bat again.
    pause
    exit /b 1
)

%PY_EXE% --version || goto fail

echo.
echo [2/30] Creating/checking local virtual environment...
if exist "%VENV_DIR%\Scripts\python.exe" (
    "%VENV_DIR%\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] in [(3,11),(3,12)] else 1)" >nul 2>nul
    if errorlevel 1 (
        echo Existing .venv is broken or does not use Python 3.11/3.12. Recreating it...
        rmdir /s /q "%VENV_DIR%" || goto fail
    )
)
if not exist "%VENV_DIR%\Scripts\python.exe" (
    %PY_EXE% -m venv "%VENV_DIR%" || goto fail
)
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo [2B/30] Ensuring pip is available in local virtual environment...
"%VENV_PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo pip is missing in the virtual environment. Running ensurepip...
    "%VENV_PY%" -m ensurepip --upgrade || goto fail
)

"%VENV_PY%" -m pip install --upgrade pip setuptools wheel || goto fail

echo.
echo [3/30] Installing Python requirements...
"%VENV_PY%" -m pip install -r "%REQ_FILE%" || goto fail

echo.
echo [3B/30] Verifying pinned BrainGlobe compatibility runtime...
"%VENV_PY%" -c "import importlib.metadata as m; v=m.version('brainglobe-atlasapi'); print('brainglobe-atlasapi=',v); raise SystemExit(0 if v=='2.3.1' else 1)" || goto fail
"%VENV_PY%" -m pip check || goto fail

echo.
echo [4/30] Running syntax smoke test...
"%VENV_PY%" -B "src\release_syntax_check_no_pycache.py" || goto fail

echo.
echo [5/30] Cleaning previous generated outputs...
if exist "src\v27_clean_generated_outputs.py" "%VENV_PY%" "src\v27_clean_generated_outputs.py" || goto fail

echo.
echo [6/30] Recording environment...
"%VENV_PY%" "src\record_environment.py" || goto fail

echo.
echo [7/30] Inspecting input files...
"%VENV_PY%" "src\inspect_inputs.py" || goto fail

echo.

echo.
echo [7B/30] Checking required Paxinos source data...
REM V32.26 DATA MANAGER AUTODOWNLOAD START
REM Ensure minimal Paxinos/Watson source data before the label analysis steps.
REM Windows fix: do not pass --root \"%~dp0\" directly because %%~dp0 ends with a backslash.
REM The trailing backslash can break argv quoting. Use %%~dp0. instead.
set "V32_26_PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%V32_26_PYEXE%" set "V32_26_PYEXE=python"
set "V32_26_ROOT=%~dp0."
echo.
echo [DATA] Checking/downloading minimal Paxinos source data...
"%V32_26_PYEXE%" "%~dp0src\release_data_manager.py" --root "%V32_26_ROOT%" --mode ensure-minimal --include-optional
if errorlevel 1 goto fail
REM V32.26 DATA MANAGER AUTODOWNLOAD END

"%VENV_PY%" "src\release_data_preflight.py" || goto missing_data
echo [8/30] Analyzing Paxinos labels...
"%VENV_PY%" "src\analyze_paxinos_labels.py" || goto fail

echo.
echo [9/30] Resolving placeholder label context...
"%VENV_PY%" "src\analyze_placeholder_context.py" || goto fail

echo.
echo [10/30] Building draft structures.json...
"%VENV_PY%" "src\build_structures_json.py" || goto fail

echo.
echo [11/30] Applying BrainGlobe root fix...
"%VENV_PY%" "src\v11_fix_brainglobe_root.py" || goto fail

echo.
echo [12/30] Cleaning labels/names for draft structures...
"%VENV_PY%" "src\v15_cleanup_structures_labels.py" --stage draft || goto fail

echo.
echo [13/30] Building hierarchical draft structures...
"%VENV_PY%" "src\v16_build_hierarchical_structures.py" --stage draft || goto fail

echo.
echo [14/30] Building provisional atlas folder...
"%VENV_PY%" "src\build_provisional_brainglobe_atlas.py" || goto fail

echo.
echo [15/30] Fixing provisional metadata...
"%VENV_PY%" "src\v13_fix_brainglobe_metadata.py" --target provisional || goto fail
"%VENV_PY%" "src\v22_fix_metadata_compliance.py" --target provisional || goto fail
"%VENV_PY%" "src\v32_fix_no_additional_references.py" --target provisional || goto fail

echo.
echo [16/30] Fixing provisional ABBA structure/root compatibility...
"%VENV_PY%" "src\v29_fix_abba_structure_root.py" --target provisional || goto fail
"%VENV_PY%" "src\v30_enrich_abba_java_structures.py" --target provisional || goto fail
"%VENV_PY%" "src\v27_validate_root_compatibility.py" --target provisional || goto fail

echo.
echo [17/30] Exporting provisional TIFFs and hemispheres...
"%VENV_PY%" "src\v14_export_brainglobe_tiffs.py" --target provisional || goto fail
"%VENV_PY%" "src\v31_create_hemispheres_tiff.py" --target provisional || goto fail
echo.
echo [17b/30] Leaving provisional atlas at native package stage...
echo Final three-channel ABBA display layout is applied only after native BrainGlobe install.

echo.
echo [18/30] Validating provisional atlas package...
"%VENV_PY%" "src\validate_provisional_atlas_package.py" || goto fail

echo.
echo [19/30] Building official candidate package...
"%VENV_PY%" "src\build_official_candidate.py" || goto fail

echo.
echo [20/30] Cleaning/enriching official candidate structures...
"%VENV_PY%" "src\v15_cleanup_structures_labels.py" --stage official || goto fail
"%VENV_PY%" "src\v16_build_hierarchical_structures.py" --stage official || goto fail
"%VENV_PY%" "src\v13_fix_brainglobe_metadata.py" --target official || goto fail
"%VENV_PY%" "src\v22_fix_metadata_compliance.py" --target official || goto fail
"%VENV_PY%" "src\v32_fix_no_additional_references.py" --target official || goto fail
"%VENV_PY%" "src\v29_fix_abba_structure_root.py" --target official || goto fail
"%VENV_PY%" "src\v30_enrich_abba_java_structures.py" --target official || goto fail
"%VENV_PY%" "src\v27_validate_root_compatibility.py" --target official || goto fail

echo.
echo [21/30] Exporting official TIFFs and hemispheres...
"%VENV_PY%" "src\v14_export_brainglobe_tiffs.py" --target official || goto fail
"%VENV_PY%" "src\v31_create_hemispheres_tiff.py" --target official || goto fail
echo.
echo [21b/30] Leaving official candidate at native package stage...
echo Final three-channel ABBA display layout is applied only after native BrainGlobe install.

echo.
echo [22/30] Installing clean native BrainGlobe atlas...
"%VENV_PY%" "src\v25_clean_native_brainglobe_install.py" --native-install --clean-install || goto fail

echo.
echo [23/30] Applying installed metadata/channel cleanup...
"%VENV_PY%" "src\v32_fix_no_additional_references.py" --target installed || goto fail

echo.
echo [23a/30] Applying curated Paxinos acronym resource to generated and installed metadata...
"%VENV_PY%" "src\apply_curated_acronym_resource.py" --root "%BUILDER_ROOT%" --apply --require-installed || goto fail
echo.
echo [23b/30] Applying final V43C three-channel ABBA display layout to installed BrainGlobe atlas...
if not exist "src\v43c_restore_v43_distance_channel.py" (
    echo ERROR: Missing src\v43c_restore_v43_distance_channel.py
    echo Extract/apply the V43C files before running this builder.
    goto fail
)
"%VENV_PY%" "src\v43c_restore_v43_distance_channel.py" --root "%BUILDER_ROOT%" --target installed --apply --strict || goto fail

echo.
echo [24/30] Installed atlas display baseline applied.

echo.
echo [25/30] Optional ABBA visibility patch...
if /I "%PATCH_ABBA%"=="ASK" (
    choice /C YN /M "Patch ABBA installations so local BrainGlobe atlases appear in ABBA?"
    if errorlevel 2 (set "PATCH_ABBA=NO") else (set "PATCH_ABBA=YES")
)
if /I "%PATCH_ABBA%"=="YES" (
    "%VENV_PY%" "src\v17_patch_abba_visibility.py" --all || goto fail
) else (
    echo Skipping ABBA patch.
)

echo.
echo [26/30] Hiding native ABBA borders display source...
if not exist "src\v44_patch_abba_python_hide_native_borders.py" (
    echo ERROR: Missing src\v44_patch_abba_python_hide_native_borders.py
    echo Extract/apply the V44 file before running this builder.
    goto fail
)
"%VENV_PY%" "src\v44_patch_abba_python_hide_native_borders.py" --root "%BUILDER_ROOT%" --apply --patch-all --fail-if-none
if errorlevel 1 (
    echo Normal ABBA Python discovery failed. Trying deep search...
    "%VENV_PY%" "src\v44_patch_abba_python_hide_native_borders.py" --root "%BUILDER_ROOT%" --apply --patch-all --deep-search --fail-if-none || goto fail
)

echo.
echo ============================================================
echo Done.
echo.
echo ABBA display settings:
echo   reference (Ch. 0)                         = ON
echo   soft_region_fill_reference (Ch. 1)         = optional
echo   distance_to_2d_outline_reference (Ch. 2)   = optional
echo   native borders display source              = hidden by V44
echo.
echo Open atlas:
echo   paxinos_watson_rat_40um
echo.
echo Reports are in: reports\
echo ============================================================
echo.
pause
endlocal
exit /b 0


:detect_python
set "PY_EXE="

where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PY_EXE=py -3.12"
        exit /b 0
    )

    py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PY_EXE=py -3.11"
        exit /b 0
    )
)

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set PY_EXE="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    exit /b 0
)

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set PY_EXE="%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    exit /b 0
)

if exist "%ProgramFiles%\Python312\python.exe" (
    set PY_EXE="%ProgramFiles%\Python312\python.exe"
    exit /b 0
)

if exist "%ProgramFiles%\Python311\python.exe" (
    set PY_EXE="%ProgramFiles%\Python311\python.exe"
    exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in [(3, 11), (3, 12)] else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PY_EXE=python"
        exit /b 0
    )
)

exit /b 0


:missing_data
echo.
echo Required Paxinos source data are missing.
echo See reports\release_data_preflight_summary.txt and reports\release_data_preflight_report.json.
echo.
echo Put minimal source files into:
echo   data\raw\bluebrainheadmodels\
echo.
echo Required:
echo   Paxinos_Watson_Atlas.nii.gz  or  Paxinos_Watson_Atlas.nii
echo   Paxinos_Watson_Labels.txt
echo Optional:
echo   Paxinos_Watson_Labels_Cortex.txt
echo.
echo No MRI/reference-channel experiment was run.
echo.
pause
exit /b 2

:fail
echo.
echo Pipeline failed. Check reports\ and console output.
echo No MRI/reference-channel experiment was run.
echo.
pause
exit /b 1
