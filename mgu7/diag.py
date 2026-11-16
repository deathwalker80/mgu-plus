import struct
from .hsfz import EnetClient, HsfzError, UdsReject, X, MGU, GATEWAY, TESTER, P_ADVANCED, P_SVK, P_READ_IP, P_ISTEP, match_response, require_response
REQUEST_SVK = bytes.fromhex('22 F1 01')
REQUEST_IP = bytes.fromhex('22 17 2A')
REQUEST_DTC = bytes.fromhex('19 02 0C')
ROUTINE_DEBUG_STOP = bytes.fromhex('31 02 A0 FD')
ROUTINE_DEBUG_RESULTS = bytes.fromhex('31 03 A0 FD')
SVK_TYPES = {1: 'HWEL', 5: 'CAFD', 6: 'BTLD', 8: 'SWFL', 13: 'SWFK'}
BTLD_MGU = '00003F36'
BTC_NBT = ('00001275', '00001FFD')
DTC_DEBUG_SSH_ACTIVE = 'B7F8E5'

class DiagError(Exception):
    pass

class SvkRecord:

    def __init__(self, rec_type, version, trip_a, trip_b, trip_c):
        self.type = rec_type
        self.name = SVK_TYPES.get(rec_type, '?%d' % rec_type)
        self.version = version
        self.trip = (trip_a, trip_b, trip_c)

    def version_hex(self):
        return '%08X' % self.version

    def trip_text(self):
        return '%03d.%03d.%03d' % self.trip

    def __str__(self):
        return '%s %s_%s' % (self.name, self.version_hex(), self.trip_text())

class Svk:

    def __init__(self, records):
        self.records = records

    def all_named(self, name):
        return [r for r in self.records if r.name == name]

    def find(self, name, version_hex=None):
        recs = self.all_named(name)
        if len(recs) != 1:
            return None
        if version_hex is not None and recs[0].version_hex() != version_hex:
            return None
        return recs[0]

    def is_supported_mgu(self):
        return self.find('BTLD', BTLD_MGU) is not None

    def is_nbt(self):
        return any((self.find('BTLD', v) is not None for v in BTC_NBT))

    def summary(self):
        return ', '.join((str(r) for r in self.records))

ID7_BTLD = '00003F36'          # il bootloader della MGU iDrive 7 (anche ID8 BDC3, per i dati finora)
MGU18_HWEL_PREFIX = '00005F3'  # hardware MGU18 (iDrive 7): visto 00005F31, 00005F32


def detect_variant(svk):
    """Riconosce la variante della head unit dai soli dati letti (SVK), senza
    sceglierla a mano. Ritorna un dict:
      code       : 'id7' quando è certo, altrimenti None (ID8/ignoto: lo conferma l'utente)
      family     : 'id7' | 'id8' | None
      confidence : 'sure' | 'partial' | 'unknown'
      it / en    : spiegazione breve
      software   : 'MMM.mmm.ppp' del bootloader, se letto
    La variante sceglie l'IMMAGINE di flash, che dipende dall'HARDWARE, non
    dalla versione software: il flash si basa sul bootloader (BTLD 00003F36) e
    va su qualsiasi I-Step, vecchio o nuovo. Quindi l'ID7 (hardware MGU18,
    HWEL 00005F3x) viene riconosciuto per ogni software (026, 030, ...). L'ID8
    (MGU20/21) non viene scelto da solo: BDC3 vs BCP lo conferma l'utente."""
    btld = None
    hwel = None
    for r in svk.records:
        if r.name == 'BTLD' and btld is None:
            btld = r
        if r.name == 'HWEL' and hwel is None:
            hwel = r
    major = btld.trip[0] if btld else None
    if major is None:
        swfl = [r for r in svk.records if r.name == 'SWFL']
        if swfl:
            major = max(r.trip[0] for r in swfl)
    sw = btld.trip_text() if btld else None
    hwel_hex = hwel.version_hex() if hwel else ''
    is_known_mgu = any(r.name == 'BTLD' and r.version_hex() == ID7_BTLD for r in svk.records)

    # MGU18 = iDrive 7: hardware HWEL 00005F3x (visto 00005F31, 00005F32), con
    # qualsiasi software. È il segnale giusto per l'immagine di flash.
    if is_known_mgu and hwel_hex.startswith(MGU18_HWEL_PREFIX):
        return {'code': 'id7', 'family': 'id7', 'confidence': 'sure', 'software': sw,
                'it': 'MGU iDrive 7 (MGU18, hardware %s, software %s): variante impostata in automatico.'
                      % (hwel_hex, sw or '?'),
                'en': 'MGU iDrive 7 (MGU18, hardware %s, software %s): variant set automatically.'
                      % (hwel_hex, sw or '?')}
    if major == 36:
        return {'code': None, 'family': 'id8', 'confidence': 'partial', 'software': sw,
                'it': 'MGU iDrive 8 (software %s): conferma tu se è BDC3 o BCP prima del flash.' % (sw or '?'),
                'en': 'MGU iDrive 8 (software %s): confirm whether it is BDC3 or BCP before flashing.' % (sw or '?')}
    if is_known_mgu:
        # È una MGU (bootloader giusto) ma l'hardware non è tra quelli noti:
        # non scelgo l'immagine da solo, la conferma l'utente.
        return {'code': None, 'family': 'mgu', 'confidence': 'partial', 'software': sw,
                'it': 'MGU riconosciuta (hardware %s, software %s): conferma tu il tipo prima del flash.'
                      % (hwel_hex or '?', sw or '?'),
                'en': 'MGU recognised (hardware %s, software %s): confirm the type before flashing.'
                      % (hwel_hex or '?', sw or '?')}
    return {'code': None, 'family': None, 'confidence': 'unknown', 'software': sw,
            'it': 'Variante non riconosciuta dai dati: scegli tu il tipo di MGU.',
            'en': 'Variant not recognised from the data: choose the MGU type yourself.'}


def parse_svk(payload):
    if not payload:
        raise DiagError('The SVK response is missing.')
    idx = payload.find(bytes.fromhex('62 F1 01'))
    if idx < 0:
        raise DiagError('The SVK response does not contain 62 F1 01.')
    body = payload[idx + 3:]
    strict_ok = idx == 0 and len(body) % 8 == 0
    records = []
    start = None
    if strict_ok:
        start = 0
    else:
        for i in range(len(body) - 16 + 1):
            if _svk_record_at(body, i) and _svk_record_at(body, i + 8):
                start = i
                break
    if start is None:
        raise DiagError('The SVK response contains no known record pairs.')
    i = start
    while i + 7 < len(body):
        if body[i] in SVK_TYPES:
            version = struct.unpack('>I', body[i + 1:i + 5])[0]
            records.append(SvkRecord(body[i], version, body[i + 5], body[i + 6], body[i + 7]))
        i += 8
    if not records:
        raise DiagError('The SVK response contains no known records.')
    return Svk(records)

def _svk_record_at(body, i):
    return i + 7 < len(body) and body[i] in SVK_TYPES and (body[i + 1] == 0) and (body[i + 2] == 0)

def read_svk(client, dst=MGU):
    raw = client.request(dst, REQUEST_SVK, P_SVK)
    x = match_response(raw, dst)
    if x is None:
        raise DiagError('The MGU returned no complete identification response.')
    x.require(REQUEST_SVK[0])
    return parse_svk(x.data)

def parse_mgu_ip(payload, dst=MGU):
    if not payload:
        raise DiagError('The MGU IP response is missing.')
    idx = payload.find(bytes.fromhex('62 17 2A 00'))
    if idx < 0:
        raise DiagError('The MGU IP response does not contain 62 17 2A 00.')
    body = payload[idx + 4:]
    if len(body) < 12:
        raise DiagError('The MGU IP response did not contain four IPv4 addresses.')
    ips = [struct.unpack('>I', body[i:i + 4])[0] for i in range(0, 12, 4)]
    texts = ['%d.%d.%d.%d' % (v >> 24 & 255, v >> 16 & 255, v >> 8 & 255, v & 255) for v in ips]
    return tuple(texts)

def read_mgu_ip(client, dst=MGU):
    raw = client.request(dst, REQUEST_IP, P_READ_IP)
    x = match_response(raw, dst)
    if x is None:
        raise DiagError('The MGU returned no IP-configuration response.')
    x.require(REQUEST_IP[0])
    return parse_mgu_ip(x.data)

DME = 0x12                        # indirizzo diagnostico della centralina motore
REQUEST_BATTERY_OBD = bytes.fromhex('01 42')   # OBD servizio 01 PID 42 = tensione di alimentazione


def read_battery(client, dst=DME):
    """Tensione della batteria a 12 V letta dalla centralina motore con OBD
    servizio 01 PID 42 (formula standard (256*A+B)/1000 V). SOLO lettura.
    Ritorna i Volt (float) oppure None se la centralina non risponde."""
    try:
        raw = client.request(dst, REQUEST_BATTERY_OBD, P_READ_IP)
    except (HsfzError, UdsReject, DiagError, OSError):
        return None
    x = match_response(raw, dst)
    if x is None:
        return None
    d = x.data
    if len(d) < 4 or d[0] != 0x41 or d[1] != 0x42:
        return None
    return ((d[2] << 8) + d[3]) / 1000.0


# Soglie (batteria a riposo ~12,6 V piena / 12,3 V a metà; >=13,2 V = c'è un
# caricabatterie o il motore è acceso). CHARGER_V è il minimo consigliato per
# un flash/codifica, LOW_V la soglia di allerta.
BATTERY_CHARGER_V = 13.2
BATTERY_FLASH_MIN_V = 13.0
BATTERY_LOW_V = 12.4
BATTERY_VERY_LOW_V = 12.0


def battery_level(volts):
    """'charger' | 'ok' | 'low' | 'verylow' | 'unknown' dalla tensione."""
    if volts is None:
        return 'unknown'
    if volts >= BATTERY_CHARGER_V:
        return 'charger'
    if volts >= BATTERY_LOW_V:
        return 'ok'
    if volts >= BATTERY_VERY_LOW_V:
        return 'low'
    return 'verylow'


def read_dtcs(client, dst=MGU, profile=P_ISTEP):
    raw = client.request(dst, REQUEST_DTC, profile)
    x = match_response(raw, dst)
    if x is None:
        raise DiagError('The MGU returned no DTC response.')
    x.require(REQUEST_DTC[0])
    codes = []
    body = x.data
    for i in range(len(body) - 2):
        code = '%02X%02X%02X' % (body[i], body[i + 1], body[i + 2])
        if code not in codes:
            codes.append(code)
    return codes

def debug_state(client, dst=MGU, include_results=False):
    state = {'debug': None, 'dtc_debug': None, 'dtcs': None}
    if include_results:
        try:
            raw = client.request(dst, ROUTINE_DEBUG_RESULTS, P_ADVANCED)
            x = match_response(raw, dst)
            if x is not None:
                x.require(ROUTINE_DEBUG_RESULTS[0])
                state['routine'] = x.data
        except (HsfzError, UdsReject):
            state['routine'] = None
    try:
        state['dtcs'] = read_dtcs(client, dst)
        state['dtc_debug'] = DTC_DEBUG_SSH_ACTIVE in state['dtcs']
    except (HsfzError, UdsReject, DiagError):
        pass
    return state

def close_ssh(client, dst=MGU):
    raw = client.request(dst, ROUTINE_DEBUG_STOP, P_ADVANCED)
    x = match_response(raw, dst)
    if x is None or not x.data or (not len(x.data)):
        return 'missing'
    if x.negative:
        raise UdsReject('The ECU rejected service 0x%02X with NRC 0x%02X.' % (x.sid, x.nrc), x.sid, x.nrc)
    if x.data[0] != 113:
        raise UdsReject('Unexpected UDS response service 0x%02X; expected 0x71.' % x.data[0], 49, 0)
    if len(x.data) >= 4:
        if x.data[1:4] != ROUTINE_DEBUG_STOP[1:4]:
            raise UdsReject('The routine stop echoed a different identifier.', 49, 0)
    return 'confirmed'
