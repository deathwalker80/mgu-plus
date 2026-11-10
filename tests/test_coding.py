"""Offline checks of the experimental coding restore (mgu7/coding.py) against a
simulated MGU. No ENET, no vehicle."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mgu7 import assets, coding, diag
from mgu7.hsfz import TYPE_DIAG, Response

VIN = 'WBA7U2C08RCN12345'
SVK = ['HWEL 00003F2A_030.016.002', 'BTLD 00003F36_001.002.003', 'SWFL 00004A11_030.016.002',
       'CAFD 00003E52_001.010.005']
CODING = {'3000': '0102', '3001': 'aabbcc', '37fe': '0123456789'}


class Rec:
    def __init__(self, text):
        self.text = text
        self.name = text.split(' ', 1)[0]

    def __str__(self):
        return self.text


def svk(records):
    return SimpleNamespace(records=[Rec(r) for r in records])


def write_backup(root, coding_blocks=CODING, vin=VIN, verified=True, records=SVK, taken='2026-11-09 10:00:00',
                 identification=None):
    folder = os.path.join(root, '%s-%s' % (taken.replace(' ', '-').replace(':', ''), vin))
    os.makedirs(folder)
    data = {'taken': taken, 'vin': vin, 'svk': records, 'cafd': records[-1], 'coding': coding_blocks,
            'identification': identification or {}, 'verified': verified}
    raw = json.dumps(data, indent=1).encode()
    Path(folder, 'backup.json').write_bytes(raw)
    Path(folder, 'SHA256SUMS').write_text('%s  backup.json\n' % hashlib.sha256(raw).hexdigest())
    return folder


class FakeMgu:
    """A head unit that answers like the restore expects, with switches to refuse."""

    def __init__(self, coding_session_first=True, refuse_did=None, seed=b'\x01' * 8, readback_wrong=None,
                 silent_write=None):
        # silent_write: None, 'stored' (no answer but written) or 'until-coding-mode'
        # (no answer and not written until setCodingMode + reset), as seen on a real ID7
        self.store = {}
        self.sent = []
        self.coding_session_first = coding_session_first
        self.coding_mode = False
        self.refuse_did = refuse_did
        self.seed = seed
        self.readback_wrong = readback_wrong
        self.resets = 0
        self.silent_write = silent_write

    def _resp(self, data):
        return Response(SimpleNamespace(payload=bytes(data), type=TYPE_DIAG, src=0x63, dst=0xF4))

    def await_one(self, dst, payload, timeout_ms):
        self.sent.append(bytes(payload))
        sid = payload[0]
        if sid == 0x10:
            if payload[1] == 0x41 and not (self.coding_session_first or self.coding_mode):
                return self._resp(b'\x7f\x10\x22')
            return self._resp(b'\x50' + payload[1:2])
        if sid == 0x31:
            if payload[2:4] == b'\x10\x0d':
                self.coding_mode = True
            return self._resp(b'\x71' + payload[1:4])
        if sid == 0x27:
            if payload[1] == 0x11:
                return self._resp(b'\x7f\x27\x12')  # the real ID7 refuses level 0x11
            if payload[1] == 0x01:
                return self._resp(bytes((0x67, payload[1])) + self.seed)
            return self._resp(bytes((0x67, payload[1])))
        if sid == 0x2E:
            did = payload[1] << 8 | payload[2]
            if did == self.refuse_did:
                return self._resp(b'\x7f\x2e\x33')
            if self.silent_write == 'until-coding-mode' and not self.coding_mode:
                return Response(None, 0, True)
            self.store[did] = bytes(payload[3:])
            if self.silent_write == 'stored':
                return Response(None, 0, True)
            return self._resp(b'\x6e' + payload[1:3])
        if sid == 0x22:
            did = payload[1] << 8 | payload[2]
            data = self.store.get(did, b'')
            if did == self.readback_wrong:
                data = b'\x00' + data
            return self._resp(b'\x62' + payload[1:3] + data)
        return self._resp(b'\x7f' + payload[:1] + b'\x11')

    def await_reset(self, dst, payload, timeout_ms):
        self.sent.append(bytes(payload))
        self.resets += 1
        return Response(None, 0, True)

    def send_suppressed(self, dst, payload):
        self.sent.append(bytes(payload))


class BackupTests(unittest.TestCase):
    def test_find_load_and_plan(self):
        with tempfile.TemporaryDirectory() as d:
            write_backup(d, taken='2026-11-01 10:00:00')
            newest = write_backup(d, taken='2026-11-09 10:00:00')
            write_backup(d, vin='WBA00000000000000', taken='2026-11-10 10:00:00')
            found = coding.find_backups(d, VIN)
            self.assertEqual([b['folder'] for b in found][0], newest)
            self.assertEqual(found[0]['blocks'], 3)
            backup = coding.load_backup(newest)
            blocks = coding.plan(backup)
            self.assertEqual([did for did, _ in blocks], [0x3000, 0x3001, 0x37FE])  # CPS last

    def test_tampered_backup_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            folder = write_backup(d)
            p = Path(folder, 'backup.json')
            p.write_bytes(p.read_bytes().replace(b'aabbcc', b'aabbcd'))
            with self.assertRaises(coding.CodingError):
                coding.load_backup(folder)

    def test_out_of_range_did_is_refused(self):
        with self.assertRaises(coding.CodingError):
            coding.plan({'coding': {'F190': '00'}})

    def test_checks_before_writing(self):
        good = {'verified': True, 'vin': VIN, 'svk': SVK}
        self.assertEqual(coding.check_backup(good, VIN, svk(SVK)), [])
        with self.assertRaises(coding.CodingError):
            coding.check_backup(dict(good, verified=False), VIN, svk(SVK))
        with self.assertRaises(coding.CodingError):
            coding.check_backup(good, 'WBA00000000000000', svk(SVK))
        with self.assertRaises(coding.CodingError):
            coding.check_backup(good, VIN, svk(['HWEL 00009999_030.016.002'] + SVK[1:]))
        other_sw = [SVK[0], SVK[1], 'SWFL 00004A11_031.000.000', SVK[3]]
        self.assertEqual(len(coding.check_backup(good, VIN, svk(other_sw))), 1)

    def test_updated_software_and_other_unit_are_refused(self):
        good = {'verified': True, 'vin': VIN, 'svk': SVK, 'identification': {'F18C': '0001348065'}}
        self.assertEqual(coding.check_backup(good, VIN, svk(SVK), bytes.fromhex('0001348065')), [])
        with self.assertRaises(coding.CodingError) as e:
            coding.check_backup(good, VIN, svk(SVK), bytes.fromhex('0001348066'))
        self.assertIn('numero di serie', str(e.exception))
        updated = [SVK[0], 'BTLD 00003F36_001.009.000'] + SVK[2:]
        with self.assertRaises(coding.CodingError) as e:
            coding.check_backup(good, VIN, svk(updated), bytes.fromhex('0001348065'))
        self.assertIn('aggiornata', str(e.exception))
        self.assertIn('E-Sys', str(e.exception))


class RestoreTests(unittest.TestCase):
    def run_restore(self, mgu, svk_after=None):
        lines = []
        with tempfile.TemporaryDirectory() as d:
            backup = coding.load_backup(write_backup(d))
            after = svk_after or svk(SVK)
            result = coding.restore(mgu, svk(SVK[:3] + ['CAFD FFFFFFFF_255.255.255']), VIN, backup,
                                    assets.Assets().raw(7), lines.append, lambda client: after,
                                    sleep=lambda s: None, reachable_wait_ms=10, reachable_poll_ms=1)
        return result, lines

    def test_full_restore(self):
        mgu = FakeMgu()
        result, lines = self.run_restore(mgu)
        self.assertEqual(mgu.store, {0x3000: b'\x01\x02', 0x3001: b'\xaa\xbb\xcc', 0x37FE: bytes.fromhex('0123456789')})
        self.assertEqual(result['written'], 3)
        self.assertTrue(result['cafd_set'])
        writes = [p for p in mgu.sent if p[0] == 0x2E]
        self.assertEqual(writes[-1][1:3], b'\x37\xfe')
        self.assertNotIn(bytes.fromhex('27 11 FF FF FF FF'), mgu.sent)  # level 0x01 is granted first
        key = [p for p in mgu.sent if p[0] == 0x27 and p[1] == 0x02]
        self.assertEqual(len(key), 1)
        self.assertEqual(len(key[0]), 134)
        self.assertEqual(mgu.sent[-1], bytes.fromhex('11 01'))
        self.assertEqual(mgu.resets, 1)

    def test_coding_mode_path_when_session_is_refused(self):
        mgu = FakeMgu(coding_session_first=False)
        result, lines = self.run_restore(mgu)
        self.assertEqual(result['written'], 3)
        self.assertIn(bytes.fromhex('31 01 10 0D'), mgu.sent)
        self.assertIn(bytes.fromhex('31 01 10 0F'), mgu.sent)
        self.assertEqual(mgu.resets, 2)

    def test_silent_write_that_was_stored_goes_on(self):
        mgu = FakeMgu(silent_write='stored')
        result, lines = self.run_restore(mgu)
        self.assertEqual(result['written'], 3)
        self.assertNotIn(bytes.fromhex('31 01 10 0D'), mgu.sent)
        self.assertTrue(any('risulta scritto' in l for l in lines))

    def test_silent_write_retries_once_in_coding_mode(self):
        mgu = FakeMgu(silent_write='until-coding-mode')
        result, lines = self.run_restore(mgu)
        self.assertEqual(result['written'], 3)
        self.assertIn(bytes.fromhex('31 01 10 0D'), mgu.sent)
        self.assertIn(bytes.fromhex('31 01 10 0F'), mgu.sent)
        self.assertEqual(mgu.resets, 2)

    def test_silent_write_in_coding_mode_too_stops(self):
        mgu = FakeMgu(silent_write='until-coding-mode', coding_session_first=True)
        mgu.coding_mode = False
        original = mgu.await_one

        def never(dst, payload, timeout_ms):
            if payload[0] == 0x2E:
                mgu.sent.append(bytes(payload))
                return Response(None, 0, True)
            return original(dst, payload, timeout_ms)
        mgu.await_one = never
        with self.assertRaises(coding.CodingError) as e:
            self.run_restore(mgu)
        self.assertIn('nessuna risposta', str(e.exception))
        self.assertEqual(len([p for p in mgu.sent if p[0] == 0x2E]), 2)  # one retry only
        self.assertEqual(mgu.sent[-1], bytes.fromhex('11 01'))

    def test_stops_at_first_refusal_and_still_leaves(self):
        mgu = FakeMgu(refuse_did=0x3001)
        with self.assertRaises(coding.CodingError) as e:
            self.run_restore(mgu)
        self.assertIn('0x3001', str(e.exception))
        self.assertNotIn(0x37FE, mgu.store)  # nothing after the refusal
        self.assertEqual(mgu.sent[-1], bytes.fromhex('11 01'))

    def test_readback_mismatch_stops(self):
        mgu = FakeMgu(readback_wrong=0x3000)
        with self.assertRaises(coding.CodingError):
            self.run_restore(mgu)
        self.assertEqual(list(mgu.store), [0x3000])

    def test_cafd_still_empty_is_reported(self):
        result, lines = self.run_restore(FakeMgu(), svk_after=svk(SVK[:3] + ['CAFD FFFFFFFF_255.255.255']))
        self.assertFalse(result['cafd_set'])

    def test_dry_run_sends_nothing(self):
        mgu = FakeMgu()
        with tempfile.TemporaryDirectory() as d:
            backup = coding.load_backup(write_backup(d))
            result = coding.restore(mgu, svk(SVK), VIN, backup, assets.Assets().raw(7), lambda *_: None,
                                    lambda c: None, dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(mgu.sent, [])


if __name__ == '__main__':
    unittest.main()
