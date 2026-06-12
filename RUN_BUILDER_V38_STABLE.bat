@echo off
setlocal
title Rat Paxinos BrainGlobe Builder - V38 Stable

cd /d G:\rat-paxinos-brainglobe-builder

echo.
echo ============================================================
echo  Rat Paxinos BrainGlobe Builder - V38 STABLE
echo ============================================================
echo.
echo One main runner. No Zenodo auto-download. Raw files must exist in:
echo data\raw\bluebrainheadmodels
echo.
echo Steps:
echo   1. check project/ABBA Python environments
echo   2. run complete builder
echo   3. force installed V33/V38 real NeuroRat reference
echo   4. verify BrainGlobe load
echo.

echo ============================================================
echo [1/4] Repair/check ABBA Python environment
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe src\v36_repair_abba_python_env.py
if errorlevel 1 goto fail

echo.
echo ============================================================
echo [2/4] Run complete builder
echo ============================================================
call run_builder.bat
if errorlevel 1 goto fail

echo.
echo ============================================================
echo [3/4] Force V33/V38 real NeuroRat MRI reference on installed atlas
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe src\v33_create_real_neurorat_reference.py --target installed
if errorlevel 1 goto fail

echo.
echo ============================================================
echo [4/4] Verify installed BrainGlobe atlas
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe -c "from brainglobe_atlasapi import BrainGlobeAtlas; import numpy as np; a=BrainGlobeAtlas('paxinos_watson_rat_40um'); print('shape',a.shape); print('reference',a.reference.shape,a.reference.dtype,a.reference.min(),a.reference.max(),float(a.reference.mean())); print('annotation',a.annotation.shape,a.annotation.dtype,a.annotation.min(),a.annotation.max()); print('hemispheres',a.hemispheres.shape,a.hemispheres.dtype,np.unique(a.hemispheres)); print('additional_references',a.metadata.get('additional_references')); print('reference_strategy',a.metadata.get('reference_strategy'))"
if errorlevel 1 goto fail

echo.
echo ============================================================
echo  V38 COMPLETE
echo ============================================================
echo.
echo Next:
echo   1. Restart ABBA/Fiji completely.
echo   2. Open paxinos_watson_rat_40um.
echo   3. If display still looks wrong, run:
echo      RUN_V34_ORIENTATION_PREVIEWS.bat
echo.
pause
exit /b 0

:fail
echo.
echo ============================================================
echo  V38 FAILED
echo ============================================================
echo.
echo Send the complete console output to ChatGPT.
echo Do not start random older BATs now, because apparently chaos has a subscription.
echo.
pause
exit /b 1
