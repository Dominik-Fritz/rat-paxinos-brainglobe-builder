@echo off
setlocal
title V37 Complete Stable Builder - Paxinos BrainGlobe

cd /d G:\rat-paxinos-brainglobe-builder

echo.
echo ============================================================
echo  V37 COMPLETE STABLE BUILDER
echo ============================================================
echo.
echo This runs the complete builder again, then ensures the installed atlas
echo uses the V33 NeuroRat MRI reference instead of the old label-edge reference.
echo.

echo ============================================================
echo [1/4] Repair/check ABBA Python environment
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe src\v36_repair_abba_python_env.py
if errorlevel 1 (
    echo.
    echo Environment repair failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo [2/4] Run complete builder
echo ============================================================
call run_builder.bat
if errorlevel 1 (
    echo.
    echo run_builder.bat failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo [3/4] Force V33 real NeuroRat MRI reference on installed atlas
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe src\v33_create_real_neurorat_reference.py --target installed
if errorlevel 1 (
    echo.
    echo V33 installed reference patch failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo [4/4] Verify installed BrainGlobe atlas
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe -c "from brainglobe_atlasapi import BrainGlobeAtlas; import numpy as np; a=BrainGlobeAtlas('paxinos_watson_rat_40um'); print('shape',a.shape); print('reference',a.reference.shape,a.reference.dtype,a.reference.min(),a.reference.max(),float(a.reference.mean())); print('annotation',a.annotation.shape,a.annotation.dtype,a.annotation.min(),a.annotation.max()); print('hemispheres',a.hemispheres.shape,a.hemispheres.dtype,np.unique(a.hemispheres)); print('additional_references',a.metadata.get('additional_references')); print('reference_strategy',a.metadata.get('reference_strategy'))"
if errorlevel 1 (
    echo.
    echo BrainGlobe load check failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  DONE
echo ============================================================
echo.
echo Now restart ABBA/Fiji completely and open:
echo paxinos_watson_rat_40um
echo.
echo Then run:
echo RUN_V34_ORIENTATION_PREVIEWS.bat
echo.
pause
