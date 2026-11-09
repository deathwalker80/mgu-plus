"""Offline checks of the MGU Plus additions to ssh_enable.py (hotspot, --key).
No ENET/SSH calls or vehicle writes."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ssh_enable
from mgu7 import store


def tool(**kw):
    args = SimpleNamespace(gateway='auto', action='check', mgu_ip=None, vin=None, key=None, yes=True,
                           force=False, verbose=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return ssh_enable.Tool(args)


class HotspotTests(unittest.TestCase):
    def test_ssh_steps_use_the_hotspot_address(self):
        for action in ssh_enable.HOTSPOT_ACTIONS:
            t = tool(gateway='hotspot', action=action)
            t.connect()
            self.assertEqual(t.mgu_ip, store.HOTSPOT_IP)
            self.assertIsNone(t.client)
        t = tool(gateway='hotspot', action='store', mgu_ip='192.168.1.50')
        t.connect()
        self.assertEqual(t.mgu_ip, '192.168.1.50')

    def test_diagnostic_steps_refuse_the_hotspot(self):
        for action in ('flash', 'enable', 'fix-key', 'disable', 'persist', 'preflight', 'status'):
            with self.assertRaises(SystemExit):
                tool(gateway='hotspot', action=action).connect()


class KeyTests(unittest.TestCase):
    def test_chosen_key_wins_and_missing_one_stops(self):
        with tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False) as f:
            f.write('KEY')
        self.assertEqual(tool(key=f.name)._load_identity(), f.name)
        Path(f.name).unlink()
        with self.assertRaises(SystemExit):
            tool(key=f.name)._load_identity()


if __name__ == '__main__':
    unittest.main()
