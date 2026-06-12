@echo off
setlocal
title V34 Orientation Preview Diagnostics

cd /d G:\rat-paxinos-brainglobe-builder

echo Running V34 orientation preview diagnostics...
C:\Users\49152\abba-python-0.11.0\python.exe src\v34_generate_orientation_previews.py
if errorlevel 1 (
    echo Failed.
    pause
    exit /b 1
)

echo.
echo Open:
echo G:\rat-paxinos-brainglobe-builder\reports\v34_orientation_previews\index.html
echo.
pause
