@echo off
setlocal
title Force installed V33 real reference

cd /d G:\rat-paxinos-brainglobe-builder

echo.
echo ============================================================
echo  FORCE V33 REAL REFERENCE ON INSTALLED ATLAS
echo ============================================================
echo.

C:\Users\49152\abba-python-0.11.0\python.exe src\v36_repair_abba_python_env.py
if errorlevel 1 (
    echo Env repair failed.
    pause
    exit /b 1
)

C:\Users\49152\abba-python-0.11.0\python.exe src\v33_create_real_neurorat_reference.py --target installed
if errorlevel 1 (
    echo V33 installed reference patch failed.
    pause
    exit /b 1
)

C:\Users\49152\abba-python-0.11.0\python.exe -c "from brainglobe_atlasapi import BrainGlobeAtlas; a=BrainGlobeAtlas('paxinos_watson_rat_40um'); print('reference_strategy',a.metadata.get('reference_strategy')); print('reference',a.reference.shape,a.reference.dtype,a.reference.min(),a.reference.max(),float(a.reference.mean()))"

echo.
echo Done. Restart ABBA/Fiji completely.
pause
