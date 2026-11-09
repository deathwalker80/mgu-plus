"""SPERIMENTALE - riscrive sulla MGU, via ENET, la codifica salvata da
"Backup codifica" (preflight.py), per non dover passare da E-Sys/BimmerCode
dopo il flash.

Nessuna fonte pubblica descrive la sequenza completa di una codifica MGU, quindi
questa è ricostruita da due cose pubbliche e verificabili:
  - i comandi di E-Sys estratti e pubblicati (sessione di codifica 10 41,
    setCodingMode 31 01 10 0D, setApplicationMode 31 01 10 0F, timbro di
    codifica CPS sul DID 37FE, ECUReset 11 01);
  - la sequenza di flash già provata di questo strumento, che la MGU accetta:
    sessione estesa, modalità, reset, sessione di lavoro, SecurityAccess 27 11
    con la chiave M7F1, scritture, setApplicationMode, reset.

Per questo ogni passo è prudente:
  - si scrivono solo i blocchi del backup di QUESTA auto (stesso VIN, stesso
    hardware e bootloader), solo nell'area di codifica 0x3000-0x3FFF;
  - al primo rifiuto della MGU ci si ferma, prima dei blocchi successivi;
  - ogni blocco scritto viene riletto e confrontato;
  - qualunque cosa succeda, alla fine si prova a rimettere la MGU in modalità
    applicazione e a riavviarla;
  - ogni comando e ogni risposta finiscono nel registro, per capire dove la MGU
    ha detto di no.
Nel caso peggiore la MGU resta senza codifica, come dopo il flash: si rimedia
con E-Sys come prima. La scrittura della codifica non tocca il firmware.
"""

import hashlib
import json
import os
import time

from .flash import (RESET_REACHABLE_POLL_MS, RESET_REACHABLE_WAIT_MS, RESPONSE_TIMEOUT_MS,
                    SecurityOperands, extract_seed)
from .hsfz import MGU, TYPE_DIAG, HsfzError, UdsReject

CODING_FIRST, CODING_LAST = 0x3000, 0x3FFF
CPS_DID = 0x37FE                     # timbro di codifica: si scrive per ultimo
CAFD_EMPTY = 'FFFFFFFF'

REQ_EXTENDED = bytes.fromhex('10 03')
REQ_CODING_SESSION = bytes.fromhex('10 41')
REQ_SET_CODING_MODE = bytes.fromhex('31 01 10 0D')
REQ_SET_APPLICATION_MODE = bytes.fromhex('31 01 10 0F')
REQ_RESET = bytes.fromhex('11 01')
REQ_TESTER_PRESENT = bytes.fromhex('3E 80')
SEED_LEVELS = (0x11, 0x01)           # quella del flash, poi la standard
TESTER_PRESENT_EVERY = 2.0           # secondi

# Risposte negative UDS (ISO 14229) che possono comparire qui, per il registro.
NRC_TEXT = {
    0x10: 'generalReject', 0x11: 'serviceNotSupported', 0x12: 'subFunctionNotSupported',
    0x13: 'incorrectMessageLengthOrInvalidFormat', 0x14: 'responseTooLong', 0x21: 'busyRepeatRequest',
    0x22: 'conditionsNotCorrect', 0x24: 'requestSequenceError', 0x31: 'requestOutOfRange',
    0x33: 'securityAccessDenied', 0x35: 'invalidKey', 0x36: 'exceededNumberOfAttempts',
    0x37: 'requiredTimeDelayNotExpired', 0x72: 'generalProgrammingFailure',
    0x7E: 'subFunctionNotSupportedInActiveSession', 0x7F: 'serviceNotSupportedInActiveSession',
}
# Una sessione appena chiesta dopo un reset può rispondere così finché la MGU riparte.
SESSION_NOT_READY = (0x22, 0x7E, 0x7F)


class CodingError(Exception):
    """Il ripristino si è fermato; il messaggio dice dove e perché."""


def _hex(data):
    return bytes(data).hex(' ').upper()


def _nrc(nrc):
    return '0x%02X %s' % (nrc, NRC_TEXT.get(nrc, ''))


# --- backup ----------------------------------------------------------------

def find_backups(root, vin=None):
    """I backup in *root* (data/preflight), dal più recente: dict con folder, vin,
    taken, cafd, blocks, verified. Con *vin* solo quelli di quell'auto."""
    found = []
    if not os.path.isdir(root):
        return found
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        path = os.path.join(folder, 'backup.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if vin and (data.get('vin') or '').upper() != vin.upper():
            continue
        found.append({'folder': folder, 'vin': data.get('vin'), 'taken': data.get('taken') or '',
                      'cafd': data.get('cafd'), 'blocks': len(data.get('coding') or {}),
                      'verified': bool(data.get('verified'))})
    found.sort(key=lambda b: b['taken'], reverse=True)
    return found


def load_backup(folder):
    """Legge un backup e ne controlla l'integrità (SHA256SUMS scritto dal backup)."""
    path = os.path.join(folder, 'backup.json')
    with open(path, 'rb') as f:
        raw = f.read()
    sums = os.path.join(folder, 'SHA256SUMS')
    if os.path.isfile(sums):
        with open(sums, 'r', encoding='utf-8') as f:
            want = {line.split(None, 1)[1].strip(): line.split(None, 1)[0]
                    for line in f if len(line.split(None, 1)) == 2}
        if want.get('backup.json') and want['backup.json'] != hashlib.sha256(raw).hexdigest():
            raise CodingError('backup.json in %s non corrisponde al suo SHA256SUMS: backup alterato' % folder)
    data = json.loads(raw.decode('utf-8'))
    data['folder'] = folder
    return data


def plan(backup):
    """I blocchi da scrivere, [(did, dati)], solo 0x3000-0x3FFF, il CPS per ultimo."""
    blocks = []
    for key, value in (backup.get('coding') or {}).items():
        did = int(key, 16)
        if not CODING_FIRST <= did <= CODING_LAST:
            raise CodingError('il backup contiene il DID 0x%04X fuori dall\'area di codifica' % did)
        data = bytes.fromhex(value)
        if not data:
            raise CodingError('il blocco 0x%04X del backup è vuoto' % did)
        blocks.append((did, data))
    blocks.sort(key=lambda b: (b[0] == CPS_DID, b[0]))
    if not blocks:
        raise CodingError('il backup non contiene blocchi di codifica')
    return blocks


def _svk_records(svk_strings, name):
    return sorted(s for s in svk_strings if s.split(' ', 1)[0] == name)


def check_backup(backup, vin, svk_now):
    """Controlli prima di scrivere: stesso VIN, backup verificato, stesso hardware
    e bootloader. Restituisce gli avvisi (non bloccanti); solleva CodingError per
    quelli bloccanti."""
    if not backup.get('verified'):
        raise CodingError('il backup non è verificato (le due letture non coincidevano): non lo riscrivo')
    if not vin or (backup.get('vin') or '').upper() != vin.upper():
        raise CodingError('il backup è del VIN %s, l\'auto collegata è %s' % (backup.get('vin'), vin or '?'))
    now = [str(r) for r in svk_now.records]
    warnings = []
    for name in ('HWEL', 'BTLD'):
        if _svk_records(backup.get('svk') or [], name) != _svk_records(now, name):
            raise CodingError('%s della MGU diverso da quello del backup: non è la stessa unità' % name)
    if _svk_records(backup.get('svk') or [], 'SWFL') != _svk_records(now, 'SWFL'):
        warnings.append('il software (SWFL) della MGU è diverso da quello del backup')
    return warnings


# --- scrittura ----------------------------------------------------------------

class Restorer:
    """Esegue la sequenza sulla MGU tramite un EnetClient già connesso."""

    def __init__(self, client, operands_blob, log, timeout_ms=RESPONSE_TIMEOUT_MS,
                 reachable_wait_ms=RESET_REACHABLE_WAIT_MS, reachable_poll_ms=RESET_REACHABLE_POLL_MS,
                 sleep=time.sleep, clock=time.monotonic):
        self.client = client
        self.operands = SecurityOperands(operands_blob)
        self.log = log
        self.timeout_ms = timeout_ms
        self.reachable_wait_ms = reachable_wait_ms
        self.reachable_poll_ms = reachable_poll_ms
        self.sleep = sleep
        self.clock = clock
        self._last_tp = 0.0
        self.coding_mode_set = False

    # trasporto
    def _send(self, payload, what):
        self.log('  TX %s  (%s)' % (_hex(payload), what))
        resp = self.client.await_one(MGU, payload, self.timeout_ms)
        if resp.timeout:
            self.log('  RX nessuna risposta')
            return None
        if resp.type != TYPE_DIAG:
            self.log('  RX frame del gateway tipo 0x%04X (MGU non raggiungibile)' % resp.type)
            return 'unreachable'
        x = resp.payload
        if x.negative:
            self.log('  RX %s  (rifiuto %s)' % (_hex(x.data), _nrc(x.nrc)))
        else:
            self.log('  RX %s' % _hex(x.data[:24]) + (' …' if len(x.data) > 24 else ''))
        return x

    def _tester_present(self, force=False):
        now = self.clock()
        if force or now - self._last_tp >= TESTER_PRESENT_EVERY:
            self.client.send_suppressed(MGU, REQ_TESTER_PRESENT)
            self._last_tp = now

    def _positive(self, x, request):
        return (x is not None and x != 'unreachable' and not x.negative and x.data
                and x.data[0] == (request[0] + 0x40) & 0xFF)

    def _session(self, request, what):
        """Chiede una sessione, aspettando la MGU se sta ancora ripartendo."""
        deadline = self.clock() + self.reachable_wait_ms / 1000.0
        while True:
            x = self._send(request, what)
            if self._positive(x, request):
                return True
            retry = x is None or x == 'unreachable' or (x.negative and x.nrc in SESSION_NOT_READY)
            if not retry or self.clock() >= deadline:
                return False
            self.sleep(self.reachable_poll_ms / 1000.0)

    def _reset(self):
        self.log('  TX %s  (ECUReset)' % _hex(REQ_RESET))
        resp = self.client.await_reset(MGU, REQ_RESET, self.timeout_ms)
        self.log('  RX %s' % ('nessuna risposta (normale durante il riavvio)' if resp.timeout
                              else _hex(resp.payload.data)))

    def _security_access(self):
        for level in SEED_LEVELS:
            x = self._send(bytes((0x27, level)) + b'\xFF\xFF\xFF\xFF', 'SecurityAccess richiesta seed 0x%02X' % level)
            if not self._positive(x, b'\x27'):
                continue
            seed = extract_seed(x.data, level)
            if seed is None:
                if len(x.data) >= 2 and not any(x.data[2:]):
                    self.log('  seed tutto a zero: accesso già concesso')
                    return True
                continue
            key = self.operands.compute_key_payload(seed, (level & 0xFE) + 2)
            k = self._send(key, 'SecurityAccess chiave M7F1')
            if self._positive(k, b'\x27'):
                return True
        return False

    def enter(self):
        """Porta la MGU in sessione di codifica, con l'accesso se lo concede."""
        self.log('Ingresso in codifica:')
        if not self._session(REQ_EXTENDED, 'sessione estesa'):
            raise CodingError('la MGU non entra in sessione estesa')
        if not self._session(REQ_CODING_SESSION, 'sessione di codifica'):
            self.log('  la sessione di codifica non è stata concessa subito: provo con setCodingMode e riavvio')
            x = self._send(REQ_SET_CODING_MODE, 'setCodingMode')
            if not self._positive(x, REQ_SET_CODING_MODE):
                raise CodingError('la MGU rifiuta setCodingMode')
            self.coding_mode_set = True
            self._reset()
            self.sleep(3.5)
            if not self._session(REQ_EXTENDED, 'sessione estesa dopo il riavvio'):
                raise CodingError('la MGU non torna in sessione estesa dopo il riavvio')
            if not self._session(REQ_CODING_SESSION, 'sessione di codifica'):
                raise CodingError('la MGU non concede la sessione di codifica')
        if self._security_access():
            self.log('  accesso di sicurezza concesso')
        else:
            self.log('  accesso di sicurezza non concesso: provo a scrivere senza (se serve, la MGU rifiuterà la prima scrittura)')

    def write(self, blocks):
        """Scrive e rilegge; si ferma al primo rifiuto. Ritorna i DID scritti."""
        self.log('Scrittura di %d blocchi (il timbro 0x%04X per ultimo):' % (len(blocks), CPS_DID))
        written = []
        for did, data in blocks:
            self._tester_present()
            req = bytes((0x2E, did >> 8, did & 0xFF)) + data
            x = self._send(req, 'scrivi 0x%04X, %d byte' % (did, len(data)))
            if not self._positive(x, req):
                why = 'nessuna risposta' if x is None else ('MGU non raggiungibile' if x == 'unreachable'
                                                            else 'rifiuto %s' % _nrc(x.nrc))
                raise CodingError('blocco 0x%04X non scritto (%s); %d blocchi scritti prima' % (did, why, len(written)))
            back = self._send(bytes((0x22, did >> 8, did & 0xFF)), 'rileggi 0x%04X' % did)
            if not self._positive(back, b'\x22') or back.data[3:] != data:
                raise CodingError('blocco 0x%04X scritto ma la rilettura non coincide' % did)
            written.append(did)
        self.log('  %d blocchi scritti e riletti identici' % len(written))
        return written

    def leave(self):
        """Sempre, anche dopo un errore: modalità applicazione e riavvio."""
        self.log('Uscita dalla codifica:')
        try:
            if self.coding_mode_set:
                self._send(REQ_SET_APPLICATION_MODE, 'setApplicationMode')
            self._reset()
        except (HsfzError, UdsReject, OSError) as e:
            self.log('  uscita non completata: %s (riavviare la head unit tenendo premuta la manopola)' % e)


def restore(client, svk_now, vin, backup, operands_blob, log, read_svk, dry_run=False, **kw):
    """Tutto il ripristino. Ritorna un dict con l'esito; solleva CodingError per i
    blocchi prima di toccare la MGU."""
    warnings = check_backup(backup, vin, svk_now)
    blocks = plan(backup)
    for w in warnings:
        log('ATTENZIONE: %s' % w)
    log('Backup: %s (preso il %s UTC, CAFD %s, %d blocchi)' % (backup['folder'], backup.get('taken'),
                                                               backup.get('cafd'), len(blocks)))
    cafd_now = next((str(r) for r in svk_now.records if str(r).startswith('CAFD')), 'nessuna')
    log('CAFD sulla MGU adesso: %s' % cafd_now)
    if dry_run:
        log('Prova a vuoto: nessun comando inviato. Blocchi che verrebbero scritti:')
        for did, data in blocks:
            log('  0x%04X  %d byte' % (did, len(data)))
        return {'written': 0, 'blocks': len(blocks), 'cafd_after': cafd_now, 'dry_run': True}
    r = Restorer(client, operands_blob, log, **kw)
    written = []
    try:
        r.enter()
        written = r.write(blocks)
    finally:
        r.leave()
    r.sleep(3.5)
    cafd_after = 'non letta'
    deadline = r.clock() + r.reachable_wait_ms / 1000.0
    while True:
        try:
            svk = read_svk(client)
            cafd_after = next((str(r2) for r2 in svk.records if str(r2).startswith('CAFD')), 'nessuna')
            break
        except (HsfzError, UdsReject, Exception) as e:  # noqa: BLE001 - la MGU sta ripartendo
            if r.clock() >= deadline:
                log('SVK non letto dopo il riavvio: %s' % e)
                break
            r.sleep(r.reachable_poll_ms / 1000.0)
    log('CAFD sulla MGU dopo il ripristino: %s' % cafd_after)
    return {'written': len(written), 'blocks': len(blocks), 'cafd_after': cafd_after, 'dry_run': False,
            'cafd_set': CAFD_EMPTY not in cafd_after and cafd_after not in ('nessuna', 'non letta')}
