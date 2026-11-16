@echo off
setlocal EnableExtensions
title MGU Plus - Diagnostica vettura
cd /d "%~dp0"

rem Trova MGU-Plus.exe: nella stessa cartella di questo file o in MGU-Plus\
set "EXE=%~dp0MGU-Plus.exe"
if not exist "%EXE%" set "EXE=%~dp0MGU-Plus\MGU-Plus.exe"
if not exist "%EXE%" (
    echo.
    echo  Non trovo MGU-Plus.exe.
    echo  Metti questo file nella stessa cartella di MGU-Plus.exe, poi rilancialo.
    echo.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   MGU Plus - lettura completa della vettura (SOLO LETTURA)
echo  ============================================================
echo.
echo  1. Collega il cavo ENET alla presa OBD della macchina.
echo  2. Accendi il quadro in modalita' diagnosi:
echo       premi il tasto Start/Stop 3 volte di fila, veloce,
echo       SENZA premere la frizione (motore spento).
echo  3. Quando sei pronto, premi un tasto qui sotto.
echo.
echo  Questo legge soltanto: non cambia NIENTE sulla macchina.
echo.
pause >nul

echo.
echo  Sto leggendo la vettura... aspetta, puo' metterci un minuto.
start /wait "" "%EXE%" --diagnostica

echo.
echo  Fatto. Si e' aperta una cartella "diagnostica".
echo  Mandami il file che si chiama  diagnostica-....txt
echo  (quello piu' recente). Grazie!
echo.
pause
