@echo off
setlocal EnableExtensions DisableDelayedExpansion
title MGU Plus - Diagnostica vettura + Cattura E-Sys
cd /d "%~dp0"

rem ===========================================================================
rem UN SOLO strumento, due cose:
rem   1) LEGGE la vettura (sola lettura) e salva un report.
rem   2) REGISTRA il traffico ENET mentre fai la codifica con E-Sys, cosi' si
rem      capisce la sequenza esatta e la si replica nel programma.
rem La registrazione usa pktmon, gia' dentro Windows 10/11: non installa niente.
rem ===========================================================================

rem --- permessi di amministratore (servono a pktmon) --------------------------
fltmc >nul 2>&1
if not errorlevel 1 goto :admin
if "%~1"=="elevato" goto :noperm
echo.
echo  Chiedo i permessi di amministratore (servono per registrare): conferma.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevato' -Verb RunAs"
exit /b 0
:noperm
echo.
echo  Senza amministratore non posso registrare. Clic destro su questo file,
echo  "Esegui come amministratore".
echo.
pause
exit /b 1

:admin
cd /d "%~dp0"

rem --- trova MGU-Plus.exe ------------------------------------------------------
set "EXE=%~dp0MGU-Plus.exe"
if not exist "%EXE%" set "EXE=%~dp0MGU-Plus\MGU-Plus.exe"
if not exist "%EXE%" (
    echo.
    echo  Non trovo MGU-Plus.exe. Metti questo file nella stessa cartella di
    echo  MGU-Plus.exe e rilancialo.
    echo.
    pause
    exit /b 1
)

set "OUT=%~dp0data\diagnostica"
if not exist "%OUT%" mkdir "%OUT%" >nul 2>&1

echo.
echo  ============================================================
echo   PASSO 1 di 2 - LEGGO la vettura (solo lettura, non cambio niente)
echo  ============================================================
echo.
echo  1. Collega il cavo ENET alla presa OBD della macchina.
echo  2. Quadro in diagnosi: premi Start/Stop 3 volte veloci, senza frizione.
echo  3. Premi un tasto quando sei pronto.
echo.
pause >nul
echo  Leggo... aspetta un minuto.
start /wait "" "%EXE%" --diagnostica
echo.
echo  Lettura fatta: il report e' nella cartella "diagnostica".
echo.

echo  ============================================================
echo   PASSO 2 di 2 - REGISTRARE la codifica con E-Sys
echo  ============================================================
echo.
echo  Serve SOLO se adesso stai per rimettere la codifica con E-Sys su questa
echo  macchina (dopo un flash). Se non lo fai ora, rispondi N: hai gia' il report.
echo.
set "GO="
set /p "GO=Vuoi registrare adesso una codifica E-Sys? (S/N): "
if /I not "%GO%"=="S" goto :fine_solo_lettura

rem --- controllo pktmon (Windows 10 2004+ / 11) -------------------------------
where pktmon.exe >nul 2>&1
if errorlevel 1 goto :no_pktmon
set "BUILD=0"
for /f "usebackq delims=" %%B in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[Environment]::OSVersion.Version.Build"`) do set "BUILD=%%B"
if %BUILD% LSS 19041 goto :no_pktmon

for /f "usebackq delims=" %%T in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Date -Format yyyyMMdd-HHmmss"`) do set "STAMP=%%T"
if not defined STAMP set "STAMP=senza-data"
set "ETL=%TEMP%\cattura-esys-%STAMP%.etl"
set "PCAP=%OUT%\cattura-esys-%STAMP%.pcapng"

rem registra TUTTO il traffico con la macchina (indirizzi 169.254.x.x)
pktmon stop >nul 2>&1
pktmon filter remove >nul 2>&1
pktmon filter add ENET -i 169.254.0.0/16 >nul
if errorlevel 1 goto :errore_filtri

pktmon start --capture --comp nics --pkt-size 0 -f "%ETL%" >nul
if not errorlevel 1 goto :registra
pktmon start --capture --pkt-size 0 -f "%ETL%" >nul
if errorlevel 1 goto :errore_avvio

:registra
echo.
echo  ------------------------------------------------------------
echo   REGISTRAZIONE IN CORSO
echo  ------------------------------------------------------------
echo  ADESSO apri E-Sys e rimetti la codifica del HU_MGU come fai di solito
echo  (inietta il CAFD e scrivi l'NCD salvato).
echo.
echo  Quando E-Sys ha finito, torna in QUESTA finestra e premi un tasto.
echo  Non chiudere questa finestra con la X.
echo.
pause >nul

echo  Fermo la registrazione e preparo il file...
pktmon stop >nul
pktmon filter remove >nul 2>&1
pktmon etl2pcap "%ETL%" --out "%PCAP%" >nul
if exist "%PCAP%" del "%ETL%" >nul 2>&1

echo.
echo  ============================================================
echo   FATTO. Mandami TUTTI i file che trovi nella cartella che si apre:
echo     - diagnostica-....txt   (la lettura della macchina)
if exist "%PCAP%" echo     - cattura-esys-....pcapng   (la registrazione di E-Sys)
if not exist "%PCAP%" echo     - cattura-esys-....etl      (la registrazione di E-Sys)
echo  ============================================================
echo.
start "" "%OUT%"
pause
exit /b 0

:no_pktmon
echo.
echo  Questo Windows non puo' registrare da solo (serve Windows 10 2004+ o 11).
echo  Hai comunque il report della lettura. Per la registrazione fammi sapere.
goto :fine_comune

:errore_filtri
echo.
echo  Non riesco a impostare la registrazione: fotografa il messaggio qui sopra.
pktmon filter remove >nul 2>&1
goto :fine_comune

:errore_avvio
echo.
echo  La registrazione non e' partita: fotografa il messaggio qui sopra.
pktmon filter remove >nul 2>&1
goto :fine_comune

:fine_solo_lettura
echo.
echo  Ok, solo lettura. Mandami il file diagnostica-....txt nella cartella che si apre.
start "" "%OUT%"

:fine_comune
echo.
pause
exit /b 0
