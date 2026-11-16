<!-- prerelease -->
## MGU Plus 1.4.1 (beta, da provare in auto)

Root SSH e App Store (mgu-store) per le head unit BMW MGU iDrive 7, da un PC Windows. In italiano o in inglese, con la stessa grafica di NBT Companion.

**Da scaricare:** `MGU-Plus-windows-1.4.1.zip` qui sotto. Scompattalo e avvia `MGU-Plus\MGU-Plus.exe` con "Esegui come amministratore". La guida è in `LEGGIMI.txt`.

### Novità della 1.4.1
- Riconoscimento automatico esteso ai software più vecchi. La MGU iDrive 7 (hardware MGU18, HWEL 00005F3x) viene riconosciuta e impostata da sola per qualsiasi versione software (026, 030, ...), non più solo 030: il flash si basa sul bootloader (00003F36) e va su ogni I-Step, vecchio o nuovo. L'ID8 e gli hardware non ancora noti restano "da confermare" a mano (sul flash la variante sbagliata è pericolosa).

### Novità della 1.4.0
- Controllo della batteria. La tensione 12 V viene letta dalla centralina motore via ENET (OBD 01 42, sola lettura) e mostrata nella pastiglia "Batteria": verde ok, verde "caricabatterie" da 13,2 V, arancione sotto 12,4 V, rosso sotto 12,0 V, grigio se la centralina non risponde.
- Prima di Flash / Ripristino / Installa Store, se la tensione è sotto 13,0 V, un avviso ricorda di collegare un caricabatterie; puoi procedere comunque (scelta tua).
- Monitor dal vivo: a riposo e collegato la tensione si aggiorna ogni ~10 s. Durante il flash/codifica la legge l'operazione stessa sulla sua sessione (nessuna seconda connessione mentre si flasha).

### Novità della 1.3.8
- Un unico strumento `Diagnostica-e-Cattura-ESys.bat` nello zip: legge la vettura (sola lettura, come prima) e, se stai per ricodificare con E-Sys, registra il traffico ENET con pktmon (già in Windows 10/11, niente da installare) così si ricava la sequenza esatta della codifica. Report e registrazione finiscono nella stessa cartella data\diagnostica.

### Novità della 1.3.7
- Riconoscimento automatico della variante: a "Connetti vettura" la MGU ID7 viene impostata da sola dai dati (bootloader 00003F36, software 030); la ID8 viene segnalata ma BDC3/BCP lo confermi tu, perché sul flash la variante sbagliata è pericolosa.
- Nuovo "Diagnostica completa" (pagina Collegamento) e avvio da riga di comando `MGU-Plus.exe --diagnostica`: legge tutto in sola lettura (variante, VIN, IP, SVK completo, CAFD, seriale, identificativi 0xF100-0xF1FF, errori) e salva un report in data\diagnostica. Nessuna scrittura.
- Nello zip c'è `Diagnostica-Vettura.bat`: doppio clic, legge la vettura e apre il report da inviare.

### Novità della 1.3.6
- Pulsante "English" / "Italiano" nell'intestazione, come in NBT Companion: cambia la lingua di tutta la finestra (pagine, messaggi e log) senza perdere campi, pagina aperta e log. La scelta resta ai prossimi avvii.

### Novità della 1.3.5
- Grafica allineata a NBT Companion: stessi colori, intestazione con logo e "by DeathWalkeR", pastiglie di stato in alto (vettura, SSH, codifica, Store, operazione in corso), pagine a sinistra (Collegamento, Accesso SSH, Codifica, App Store), pulsanti arrotondati, log completo in basso.
- Backup e ripristino della codifica hanno una pagina tutta loro.

### Novità della 1.3.4
- Nuovo logo di MGU Plus, nell'intestazione e nell'icona del programma.
- Nell'intestazione la firma "by DeathWalkeR".

### Novità della 1.3.3
- Se la MGU riporta un bootloader diverso da quello del backup, il programma rilegge le versioni dopo 10 secondi prima di fermarsi (una lettura fatta mentre la MGU si sta ancora avviando può non essere quella definitiva). Se è ancora diverso si ferma senza mandare niente alla MGU, mostra le due versioni e non dà per scontato che sia stata aggiornata.

### Novità della 1.3.2
Correzioni del ripristino della codifica dopo la prima prova su una MGU ID7 vera:
- Accesso di sicurezza: usa subito il livello 0x01, l'unico che la MGU accetta (lo 0x11 viene rifiutato).
- Se la MGU non risponde a una scrittura, il programma rilegge il blocco: se è già scritto prosegue, altrimenti passa in modalità codifica come E-Sys (setCodingMode e riavvio) e riprova una volta. Attesa per ogni scrittura portata a 30 secondi.
- Controllo dell'unità anche sul numero di serie, oltre a VIN e hardware.
- Se la MGU è stata aggiornata a un'altra versione dopo il backup (bootloader diverso) il ripristino si ferma e lo spiega: in quel caso la codifica va rifatta da FA (VO) con E-Sys. Il modulo software riscritto dal flash di debug è atteso e non blocca.

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
