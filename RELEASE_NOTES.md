## MGU Plus 1.2.0

Root SSH e App Store (mgu-store) per le head unit BMW MGU iDrive 7, da un PC Windows. Tutto in una schermata, in italiano.

**Da scaricare:** `MGU-Plus-windows-1.2.0.zip` qui sotto. Scompattalo e avvia `MGU-Plus\MGU-Plus.exe` con "Esegui come amministratore". La guida è in `LEGGIMI.txt`.

### Cosa fa
- **Connessione vettura:** via cavo ENET o hotspot Wi-Fi dell'auto; legge VIN, IP, seriale e hardware della MGU.
- **Accesso SSH MGU:** Flash + Apri SSH, Apri SSH, Correggi chiave, Backup codifica (sola lettura), Disattiva SSH; scelta della chiave privata.
- **Applicazioni MGU:** versioni installate contro le ultime pubblicate, Installa / Aggiorna, Rimuovi Store, Riavvia MGU, Controlla aggiornamenti GitHub.

### Da sapere
- Il flash cancella la codifica della head unit: va ripristinata con BimmerCode o E-Sys prima di installare lo Store.
- mgu-store non è incluso: il suo installer ufficiale viene scaricato e verificato (SHA256) a ogni installazione, quindi si installa sempre l'ultima versione.
- Basato su [idrive7-8-ssh-root](https://github.com/cauliflowermossgrape/idrive7-8-ssh-root) v1.3.1 (pubblico dominio) e [mgu-store](https://github.com/robertnisipeanu/mgu-store) (GPL-3.0).
