@echo off
setlocal
title V33 Paxinos ABBA diagnostics
cd /d G:\rat-paxinos-brainglobe-builder
echo ==================== V33 REPORTS ====================
echo.
echo ===== v33_reference_source_report.txt =====
type reports\v33_reference_source_report.txt
echo.
echo ===== v33_geometry_match_report.txt =====
type reports\v33_geometry_match_report.txt
echo.
echo ===== v33_intensity_report.txt =====
type reports\v33_intensity_report.txt
echo.
echo ===== v33_final_reference_validation.txt =====
type reports\v33_final_reference_validation.txt
echo.
echo ==================== INSTALLED FILES ====================
dir "%USERPROFILE%\.brainglobe\paxinos_watson_rat_40um_v1.0"
echo.
echo ==================== INSTALLED REFERENCE CONTENT ====================
C:\Users\49152\abba-python-0.11.0\python.exe -c "import tifffile, numpy as np; a=tifffile.imread(r'%USERPROFILE%\.brainglobe\paxinos_watson_rat_40um_v1.0\reference.tiff'); print('shape=',a.shape); print('dtype=',a.dtype); print('min=',a.min()); print('max=',a.max()); print('mean=',float(a.mean())); print('p1,p50,p99=',np.percentile(a,[1,50,99]))"
echo.
echo ==================== BRAIN GLOBE LOAD CHECK ====================
cd /d C:\Users\49152\abba-python-0.11.0
python -c "from brainglobe_atlasapi import BrainGlobeAtlas; import numpy as np; a=BrainGlobeAtlas('paxinos_watson_rat_40um'); print('shape',a.shape); print('reference',a.reference.shape,a.reference.dtype,a.reference.min(),a.reference.max(),float(a.reference.mean())); print('annotation',a.annotation.shape,a.annotation.dtype,a.annotation.min(),a.annotation.max()); print('hemispheres',a.hemispheres.shape,a.hemispheres.dtype,np.unique(a.hemispheres)); print('additional_references',a.metadata.get('additional_references'))"
echo.
pause
