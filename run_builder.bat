@echo off
setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0"

title Rat Paxinos BrainGlobe Builder - V32.2 Oriented Reference Prep

echo.
echo ============================================================
echo  Rat Paxinos BrainGlobe Builder - V32.2 ORIENTED REFERENCE PREP
echo ============================================================
echo.
echo Stable builder line with validated ABBA orientation fix.
echo This still does NOT run the invalid V33 NeuroRat reference replacement.
echo The main atlas uses the provisional label-edge reference until SIGMA is validated.
echo Use RUN_SIGMA_REFERENCE_EXPERIMENT.bat for reference-background research.
echo.

set "PY_EXE="
set "VENV_DIR=.venv"
set "REQ_FILE=requirements.txt"
set "PATCH_ABBA=ASK"

for %%A in (%*) do (
    if /I "%%~A"=="--patch-abba" set "PATCH_ABBA=YES"
    if /I "%%~A"=="--no-patch-abba" set "PATCH_ABBA=NO"
)

echo Required raw files must already exist in:
echo   data\raw\bluebrainheadmodels
echo.

echo [01/34] Finding Python...
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3.11 --version >nul 2>nul
    if !ERRORLEVEL!==0 (
        set "PY_EXE=py -3.11"
    ) else (
        py -3.12 --version >nul 2>nul
        if !ERRORLEVEL!==0 (
            set "PY_EXE=py -3.12"
        ) else (
            py -3 --version >nul 2>nul
            if !ERRORLEVEL!==0 set "PY_EXE=py -3"
        )
    )
)
if "%PY_EXE%"=="" (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 set "PY_EXE=python"
)
if "%PY_EXE%"=="" (
    echo Python was not found.
    choice /C YN /M "Install Python 3.11 via winget now?"
    if errorlevel 2 (
        echo Aborted. Install Python 3.11 manually and run again.
        pause
        exit /b 1
    )
    where winget >nul 2>nul
    if errorlevel 1 (
        echo winget was not found. Install Python manually.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.11 -e --source winget
    echo Please restart this terminal/window and run again.
    pause
    exit /b 0
)
%PY_EXE% --version || goto fail

echo.
echo [02/34] Checking local virtual environment...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    %PY_EXE% -m venv "%VENV_DIR%" || goto fail
)
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo [03/34] Installing/updating Python requirements...
"%VENV_PY%" -m pip install --upgrade pip || goto fail
"%VENV_PY%" -m pip install -r "%REQ_FILE%" || goto fail

echo.
echo [04/34] Running syntax smoke test...
"%VENV_PY%" "src\v28_syntax_smoke_test.py" || goto fail

echo.
echo [05/34] Cleaning generated output folders from previous runs...
"%VENV_PY%" "src\v27_clean_generated_outputs.py" || goto fail

echo.
echo [06/34] Recording environment versions...
"%VENV_PY%" "src\record_environment.py" || goto fail

echo.
echo [07/34] Inspecting raw inputs...
"%VENV_PY%" "src\inspect_inputs.py" || goto fail

echo.
echo [08/34] Analyzing Paxinos labels...
"%VENV_PY%" "src\analyze_paxinos_labels.py" || goto fail

echo.
echo [09/34] Analyzing placeholder labels...
"%VENV_PY%" "src\analyze_placeholder_context.py" || goto fail

echo.
echo [10/34] Building draft structures.json...
"%VENV_PY%" "src\build_structures_json.py" || goto fail

echo.
echo [11/34] Cleaning draft structure labels...
"%VENV_PY%" "src\v15_cleanup_structures_labels.py" --stage draft || goto fail

echo.
echo [12/34] Building draft hierarchy...
"%VENV_PY%" "src\v16_build_hierarchical_structures.py" --stage draft || goto fail

echo.
echo [13/34] Building provisional BrainGlobe atlas folder...
"%VENV_PY%" "src\build_provisional_brainglobe_atlas.py" || goto fail

echo.
echo [14/34] Fixing provisional metadata for BrainGlobe/ABBA...
"%VENV_PY%" "src\v22_fix_metadata_compliance.py" --target provisional || goto fail

echo.
echo [15/34] Normalizing provisional root to 997...
"%VENV_PY%" "src\v29_fix_abba_structure_root.py" --target provisional || goto fail

echo.
echo [16/34] Enriching provisional structures for ABBA Java helpers...
"%VENV_PY%" "src\v30_enrich_abba_java_structures.py" --target provisional || goto fail

echo.
echo [17/34] Ensuring provisional additional_references is empty...
"%VENV_PY%" "src\v32_fix_no_additional_references.py" --target provisional || goto fail

echo.
echo [18/34] Applying validated ABBA orientation to provisional NIfTI files...
"%VENV_PY%" "src\v32_2_apply_validated_abba_orientation.py" --target provisional || goto fail

echo.
echo [19/34] Exporting provisional TIFF files...
"%VENV_PY%" "src\v14_export_brainglobe_tiffs.py" --target provisional || goto fail

echo.
echo [20/34] Creating masked provisional hemispheres.tiff...
"%VENV_PY%" "src\v31_create_hemispheres_tiff.py" --target provisional || goto fail

echo.
echo [21/34] Validating provisional atlas package...
"%VENV_PY%" "src\validate_provisional_atlas_package.py" || goto fail

echo.
echo [22/34] Building official candidate layout...
"%VENV_PY%" "src\build_official_candidate.py" || goto fail

echo.
echo [23/34] Fixing official metadata for BrainGlobe/ABBA...
"%VENV_PY%" "src\v22_fix_metadata_compliance.py" --target official || goto fail

echo.
echo [24/34] Cleaning official structure labels...
"%VENV_PY%" "src\v15_cleanup_structures_labels.py" --stage official || goto fail

echo.
echo [25/34] Building official hierarchy...
"%VENV_PY%" "src\v16_build_hierarchical_structures.py" --stage official || goto fail

echo.
echo [26/34] Normalizing official root to 997...
"%VENV_PY%" "src\v29_fix_abba_structure_root.py" --target official || goto fail

echo.
echo [27/34] Enriching official structures for ABBA Java helpers...
"%VENV_PY%" "src\v30_enrich_abba_java_structures.py" --target official || goto fail

echo.
echo [28/34] Ensuring official additional_references is empty...
"%VENV_PY%" "src\v32_fix_no_additional_references.py" --target official || goto fail

echo.
echo [29/34] Reconfirming validated ABBA orientation on official NIfTI files...
"%VENV_PY%" "src\v32_2_apply_validated_abba_orientation.py" --target official || goto fail

echo.
echo [30/34] Exporting official TIFF files...
"%VENV_PY%" "src\v14_export_brainglobe_tiffs.py" --target official || goto fail

echo.
echo [31/34] Creating masked official hemispheres.tiff...
"%VENV_PY%" "src\v31_create_hemispheres_tiff.py" --target official || goto fail

echo.
echo [32/34] Validating official root compatibility...
"%VENV_PY%" "src\v27_validate_root_compatibility.py" --target official || goto fail

echo.
echo [33/34] Installing clean native BrainGlobe atlas...
"%VENV_PY%" "src\v25_clean_native_brainglobe_install.py" --native-install --clean-install
if errorlevel 1 (
    echo Native install/load failed.
    echo Review reports\v25_native_install_report.txt and reports\v25_final_status.txt
    pause
    exit /b 1
)

echo.
echo [34/34] Final installed metadata/channel cleanup...
"%VENV_PY%" "src\v32_fix_no_additional_references.py" --target installed || goto fail
"%VENV_PY%" "src\v31_create_hemispheres_tiff.py" --target installed || goto fail

echo.
echo Optional ABBA visibility patch...
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
echo Done.
echo Review these reports first:
echo   reports\v25_final_status.txt
echo   reports\v27_root_validator_report_official.txt
echo   reports\v31_hemispheres_report_official.txt
echo   reports\v32_2_abba_orientation_report_official.txt
echo   reports\v32_no_additional_refs_report_installed.txt
echo.
echo Restart ABBA/Fiji completely and open:
echo   paxinos_watson_rat_40um
echo.
echo Expected ABBA behavior after V32.2:
echo   Coronal    = coronal and upright
echo   Sagittal   = sagittal
echo   Horizontal = horizontal
echo.
pause
popd
endlocal
exit /b 0

:fail
echo.
echo Pipeline failed. No raw data were modified.
echo Check reports\ and the console output above.
pause
popd
endlocal
exit /b 1
