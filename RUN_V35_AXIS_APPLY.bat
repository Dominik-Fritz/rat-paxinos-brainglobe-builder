@echo off
setlocal
title V35 Axis Variant APPLY

cd /d G:\rat-paxinos-brainglobe-builder

echo WARNING: This overwrites installed reference/annotation/hemispheres TIFFs.
echo A backup folder will be created inside:
echo C:\Users\49152\.brainglobe\paxinos_watson_rat_40um_v1.0
echo.

set /p VARIANT=Enter variant name to APPLY: 

C:\Users\49152\abba-python-0.11.0\python.exe src\v35_apply_axis_variant.py --variant %VARIANT% --apply
if errorlevel 1 (
    echo Failed.
    pause
    exit /b 1
)

echo.
echo Done. Restart ABBA/Fiji completely and open paxinos_watson_rat_40um.
echo.
pause
