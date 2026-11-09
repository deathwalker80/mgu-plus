<!-- prerelease -->
## MGU Plus 1.3.1 (beta, da provare in auto)

Root SSH e App Store (mgu-store) per le head unit BMW MGU iDrive 7, da un PC Windows. Tutto in una schermata, in italiano.

**Da scaricare:** `MGU-Plus-windows-1.3.1.zip` qui sotto. Scompattalo e avvia `MGU-Plus\MGU-Plus.exe` con "Esegui come amministratore". La guida è in `LEGGIMI.txt`.

### Novità della 1.3.1
- "Aggiornamenti MGU Plus" segnala anche le versioni beta (pre-release), con un avviso che non sono ancora provate in auto.

### Novità della 1.3.0
- **Ripristino della codifica senza E-Sys (SPERIMENTALE):** riscrive sulla MGU, via ENET, i blocchi del "Backup codifica" di quella stessa auto, li rilegge uno per uno e si ferma al primo rifiuto; alla fine controlla che la MGU riporti di nuovo la sua CAFD. Va provato con BimmerCode o E-Sys a portata di mano.
- Prima del flash, se manca il backup della codifica per quel VIN, il programma lo propone e lo fa da solo; dopo il flash propone il ripristino.
- Mentre legge le versioni di mgu-store da GitHub lo dice nel riquadro, e se non ci riesce spiega perché.

### Novità della 1.2.2
- Tolto il campo della chiave SSH: il programma usa da solo la chiave giusta, non c'è niente da scegliere.

### Novità della 1.2.1
- All'avvio legge subito da GitHub le ultime versioni di mgu-store e le mostra nella tabella, senza aspettare la vettura.
- iDrive 8 indicato come sperimentale (intestazione e tipo MGU): il root SSH lo supporta, lo Store è provato solo in emulatore.

### Cosa fa
- **Connessione vettura:** via cavo ENET o hotspot Wi-Fi dell'auto; legge VIN, IP, seriale e hardware della MGU.
- **Accesso SSH MGU:** Flash + Apri SSH, Apri SSH, Correggi chiave, Backup codifica (sola lettura), Disattiva SSH.
- **Applicazioni MGU:** versioni installate contro le ultime pubblicate, Installa / Aggiorna, Rimuovi Store, Riavvia MGU, Controlla aggiornamenti GitHub.

### Da sapere
- Il flash cancella la codifica della head unit: va ripristinata (dal programma, sperimentale, oppure con BimmerCode o E-Sys) prima di installare lo Store.
- mgu-store non è incluso: il suo installer ufficiale viene scaricato e verificato (SHA256) a ogni installazione, quindi si installa sempre l'ultima versione.
- Basato su [idrive7-8-ssh-root](https://github.com/cauliflowermossgrape/idrive7-8-ssh-root) v1.3.1 (pubblico dominio) e [mgu-store](https://github.com/robertnisipeanu/mgu-store) (GPL-3.0).
