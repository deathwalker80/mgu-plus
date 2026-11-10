@echo off
REM Build MGU Plus into a Windows .exe (one-folder app) and pack it as
REM dist\MGU-Plus-windows-<VERSION>.zip. Run it from the project folder, with Python on PATH.
REM PyInstaller works in a temporary folder: at the end only dist\ is left here.
setlocal
cd /d "%~dp0"
set "VERSION=1.3.3"
set "WORK=%TEMP%\mgu-plus-build"

python -c "import sys" >nul 2>nul || (echo Python not found: install it from python.org, ticking "Add python.exe to PATH", then turn off the python.exe / python3.exe app execution aliases. & goto :err)

echo === Installing dependencies (cryptography, pyinstaller) ===
python -m pip install --upgrade pip || goto :err
python -m pip install -r requirements.txt pyinstaller || goto :err

echo === Offline checks ===
python -m unittest discover -s tests || goto :err

echo === Building with PyInstaller ===
if exist "dist\MGU-Plus" rmdir /s /q "dist\MGU-Plus"
python -m PyInstaller --noconfirm --workpath "%WORK%" --distpath dist MGU-Plus.spec || goto :err
if exist "%WORK%" rmdir /s /q "%WORK%"
if exist build rmdir /s /q build

echo === Copying runtime files next to the exe ===
xcopy /E /I /Y data\assets "dist\MGU-Plus\data\assets" >nul || goto :err
copy /Y sshd-always-on.sh "dist\MGU-Plus\" >nul || goto :err
copy /Y LEGGIMI.txt "dist\MGU-Plus\" >nul || goto :err
copy /Y LICENSE "dist\MGU-Plus\" >nul || goto :err

echo === Packing the zip ===
if exist "dist\MGU-Plus-windows-%VERSION%.zip" del "dist\MGU-Plus-windows-%VERSION%.zip"
powershell -NoProfile -Command "Compress-Archive -Path dist\MGU-Plus -DestinationPath dist\MGU-Plus-windows-%VERSION%.zip" || goto :err

echo.
echo Done.
echo   Program:          dist\MGU-Plus\MGU-Plus.exe   (right click, Run as administrator)
echo   Zip for friends:  dist\MGU-Plus-windows-%VERSION%.zip
echo Keep the whole dist\MGU-Plus folder together: the keys are saved in its data\enrollments.
goto :eof

:err
echo.
echo BUILD FAILED (see the error above).
exit /b 1
