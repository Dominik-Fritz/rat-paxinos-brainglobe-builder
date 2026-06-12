@echo off
setlocal
title V36 ABBA/Paxinos stable diagnostics

cd /d G:\rat-paxinos-brainglobe-builder

echo.
echo ============================================================
echo  V36 ABBA Python environment repair
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
echo  Verify ABBA Python imports
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe -c "import sys; print(sys.executable); import nibabel,numpy,matplotlib,tifffile,pandas; print('ABBA PYTHON IMPORTS OK')"
if errorlevel 1 (
    echo.
    echo Import verification failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  V34 orientation previews
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe src\v34_generate_orientation_previews.py
if errorlevel 1 (
    echo.
    echo V34 failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  BrainGlobe atlas load check
echo ============================================================
C:\Users\49152\abba-python-0.11.0\python.exe -c "from brainglobe_atlasapi import BrainGlobeAtlas; import numpy as np; a=BrainGlobeAtlas('paxinos_watson_rat_40um'); print('shape',a.shape); print('reference',a.reference.shape,a.reference.dtype,a.reference.min(),a.reference.max(),float(a.reference.mean())); print('annotation',a.annotation.shape,a.annotation.dtype,a.annotation.min(),a.annotation.max()); print('hemispheres',a.hemispheres.shape,a.hemispheres.dtype,np.unique(a.hemispheres)); print('additional_references',a.metadata.get('additional_references')); print('reference_strategy',a.metadata.get('reference_strategy'))"

echo.
echo ============================================================
echo  NEXT STEP
echo ============================================================
echo.
echo If reference_strategy still says provisional_label_edge_reference_generated_from_annotation_boundaries,
echo run RUN_FORCE_INSTALLED_V33_REFERENCE.bat before judging the display.
echo.
echo Open:
echo G:\rat-paxinos-brainglobe-builder\reports\v34_orientation_previews\index.html
echo.
echo Send screenshots of the most plausible candidate pages or tell Chat the candidate name.
echo.
pause
