@echo off
setlocal
title V35 Axis Variant Dry Run

cd /d G:\rat-paxinos-brainglobe-builder

echo Available variants:
echo identity_xyz
echo swap_xz_zyx
echo swap_xy_yxz
echo swap_yz_xzy
echo identity_flip_z
echo identity_flip_y
echo identity_flip_x
echo swap_xz_flip_z
echo swap_xz_flip_x
echo swap_yz_flip_z
echo.

set /p VARIANT=Enter variant name for dry run: 

C:\Users\49152\abba-python-0.11.0\python.exe src\v35_apply_axis_variant.py --variant %VARIANT%
pause
