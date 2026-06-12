@echo off
setlocal EnableExtensions
pushd "%~dp0"

echo ============================================================
echo  View installed Paxinos atlas in napari
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Run run_builder.bat first.
    pause
    exit /b 1
)

set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install napari PyQt5 tifffile numpy || goto fail
"%PY%" "tools\view_installed_paxinos_in_napari.py" || goto fail

popd
endlocal
exit /b 0

:fail
echo.
echo Napari viewer failed. If Qt/PyQt complains, try installing napari[all] manually in the venv.
pause
popd
endlocal
exit /b 1
