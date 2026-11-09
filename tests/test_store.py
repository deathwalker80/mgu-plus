"""Offline checks of the Store installer step (mgu7/store.py). Nothing is
downloaded from the internet and no head unit is contacted."""
import gzip
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mgu7 import store


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class AssetTests(unittest.TestCase):
    def test_asset_names(self):
        self.assertEqual(store.asset_name('Windows', 'AMD64'), 'mgu-install-windows-x64.exe.gz')
        self.assertEqual(store.asset_name('Windows', 'ARM64'), 'mgu-install-windows-arm64.exe.gz')
        self.assertEqual(store.asset_name('Linux', 'x86_64'), 'mgu-install-linux-x64.gz')
        self.assertEqual(store.asset_name('Darwin', 'arm64'), 'mgu-install-macos-arm64.gz')
        with self.assertRaises(store.StoreError):
            store.asset_name('Windows', 'x86')

    def test_parse_sums(self):
        a, b = '0' * 64, 'A' * 64
        sums = store.parse_sums('%s  mgu-install-windows-x64.exe.gz\n%s *mgu-install-linux-x64.gz\njunk\n' % (a, b))
        self.assertEqual(sums, {'mgu-install-windows-x64.exe.gz': a, 'mgu-install-linux-x64.gz': b.lower()})

    def test_classify(self):
        self.assertEqual(store.classify(0, ['x', store.LINE_RESTART]), store.EXIT_RESTART_NEEDED)
        self.assertEqual(store.classify(0, ['Setup is done: the Store installs and updates everything from here on.']),
                         store.EXIT_DONE)
        self.assertEqual(store.classify(2, [store.LINE_CODING + ': it holds default values.']), store.EXIT_REFUSED)
        self.assertEqual(store.classify(1, [store.LINE_RESTART]), store.EXIT_FAILED)


class InstallerDownloadTests(unittest.TestCase):
    NAME = 'mgu-install-linux-x64.gz'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.release = os.path.join(self.tmp.name, 'release')
        self.cache = os.path.join(self.tmp.name, 'cache')
        os.makedirs(self.release)
        self.payload = b'#!/bin/sh\necho installer\n'
        self.gz = gzip.compress(self.payload)
        Path(self.release, self.NAME).write_bytes(self.gz)
        Path(self.release, 'SHA256SUMS').write_text('%s  %s\n' % (_sha(self.gz), self.NAME))
        self.base = Path(self.release).as_uri()
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, base=None):
        return store.ensure_installer(self.lines.append, base=base or self.base, folder=self.cache, name=self.NAME)

    def test_verified_download_and_offline_reuse(self):
        exe = self.fetch()
        self.assertEqual(Path(exe).read_bytes(), self.payload)
        self.assertTrue(any(line.startswith('Installer verified') for line in self.lines))
        # no internet at the car: the copy kept from the last run is used
        exe = self.fetch(base=Path(self.tmp.name, 'missing').as_uri())
        self.assertEqual(Path(exe).read_bytes(), self.payload)
        self.assertIn('Using the copy of the installer downloaded earlier.', self.lines)

    def test_mismatch_is_refused_and_nothing_kept(self):
        Path(self.release, 'SHA256SUMS').write_text('%s  %s\n' % ('1' * 64, self.NAME))
        with self.assertRaises(store.StoreError):
            self.fetch()
        self.assertFalse(os.path.exists(os.path.join(self.cache, self.NAME[:-3])))
        self.assertFalse(os.path.exists(os.path.join(self.cache, self.NAME)))

    def test_offline_without_a_copy_fails_clearly(self):
        with self.assertRaises(store.StoreError) as e:
            self.fetch(base=Path(self.tmp.name, 'missing').as_uri())
        self.assertIn('no internet', str(e.exception))


@unittest.skipIf(os.name == 'nt', 'uses a POSIX shell script as a stand-in installer')
class RunInstallerTests(unittest.TestCase):
    def test_arguments_and_streamed_output(self):
        with tempfile.TemporaryDirectory() as d:
            key = Path(d, 'id_rsa')
            key.write_text('KEY')
            args_file = Path(d, 'args')
            fake = Path(d, 'mgu-install')
            fake.write_text('#!/bin/sh\nprintf "%%s\\n" "$@" > "%s"\necho "connecting to 169.254.1.9:22 as given"\n'
                            'echo "%s"\nexit 0\n' % (args_file, store.LINE_RESTART))
            fake.chmod(0o755)
            lines = []
            rc, out = store.run_installer(str(fake), '169.254.1.9', str(key), lines.append)
            self.assertEqual(rc, 0)
            self.assertEqual(store.classify(rc, out), store.EXIT_RESTART_NEEDED)
            self.assertEqual(lines, ['connecting to 169.254.1.9:22 as given', store.LINE_RESTART])
            argv = args_file.read_text().splitlines()
            self.assertEqual(argv[:2], ['first-install', '--identity'])
            self.assertNotEqual(argv[2], str(key))  # a locked-down temporary copy
            self.assertEqual(argv[3:6], ['--host', '169.254.1.9', '--no-restart-prompt'])
            self.assertTrue(argv[7].startswith('UserKnownHostsFile='))
            self.assertTrue(argv[9].startswith('GlobalKnownHostsFile='))
            self.assertFalse(os.path.exists(argv[2]))  # removed after the run

    def test_install_reports_missing_coding(self):
        lines = []
        with patch.object(store.sshcli, 'available', return_value=True), \
             patch.object(store, 'ensure_installer', return_value='mgu-install'), \
             patch.object(store, 'run_installer',
                          return_value=(2, [store.LINE_CODING + ': it holds default values. ...'])):
            self.assertEqual(store.install('169.254.1.9', 'key', lines.append), store.EXIT_REFUSED)
        self.assertTrue(lines[-1].startswith("STOPPED: the head unit's coding is missing"))


class RestartTests(unittest.TestCase):
    def test_outcomes(self):
        for result, expected in (((0, '', ''), 0), ((3, '', ''), 1), ((0, '[Error. busy]', ''), 1), ((255, '', 'timeout'), 1)):
            lines = []
            with patch.object(store.sshcli, 'run', return_value=result) as run:
                self.assertEqual(store.restart('169.254.1.9', 'key', lines.append), expected)
                self.assertEqual(run.call_args.args[2], store.RESTART)
            if result == (0, '[Error. busy]', ''):
                self.assertEqual(lines, ['The head unit did not take the restart: busy'])


if __name__ == '__main__':
    unittest.main()


class VersionCheckTests(unittest.TestCase):
    CATALOG = {'commit': 'abc', 'components': [
        {'id': 'mgu-deploy', 'kind': 'tool', 'title': 'Deploy tool', 'version': '0.8.8',
         'texts': {'it': {'title': 'Strumento di distribuzione'}}},
        {'id': 'mgu-render', 'kind': 'tool', 'title': 'Renderer', 'version': '0.6.15', 'optional': True,
         'mguHmi': ['ID7', 'ID8']},
        {'id': 'id8-only', 'kind': 'app', 'title': 'Only ID8', 'version': '1.0.0', 'mguHmi': ['ID8']},
        {'id': 'robertn_store', 'kind': 'app', 'title': 'Store', 'version': '0.9.33'},
        {'kind': 'broken'},
    ]}

    def test_versions_compare_numerically(self):
        self.assertEqual(store.parse_version('0.9.33'), (0, 9, 33))
        self.assertEqual(store.parse_version('1.2'), (1, 2, 0))
        self.assertEqual(store.parse_version('1.2.3-beta'), (1, 2, 3))
        self.assertLess(store.parse_version('0.9.9'), store.parse_version('0.9.10'))

    def test_rows_compare_and_summary(self):
        rows = store.catalog_rows(self.CATALOG)
        self.assertEqual([r['id'] for r in rows], ['mgu-deploy', 'mgu-render', 'id8-only', 'robertn_store'])
        self.assertEqual(rows[0]['titles'], {'en': 'Deploy tool', 'it': 'Strumento di distribuzione'})
        installed = {'hmi': 'ID7', 'components': {'mgu-deploy': '0.8.10', 'robertn_store': '0.9.9'}}
        compared = store.compare(rows, installed)
        self.assertEqual([(r['id'], r['state']) for r in compared],
                         [('mgu-deploy', store.ROW_NEWER), ('mgu-render', store.ROW_MISSING),
                          ('robertn_store', store.ROW_UPDATE)])
        self.assertEqual(store.summarize(compared, installed),
                         {'installed': True, 'store': '0.9.9', 'updates': 1, 'missing': 1})
        never = store.compare(rows, None)
        self.assertEqual(len(never), 4)
        self.assertFalse(store.summarize(never, None)['installed'])

    def test_read_installed(self):
        status = ('{"version":"0.8.8","unit":{"hmi":"ID7","firmware":"S15C-24-07"},'
                  '"components":[{"id":"robertn_store","kind":"app","version":"0.9.30"},'
                  '{"id":"mgu-deploy","kind":"tool","version":"0.8.8"}],'
                  '"pending_reboot":[{"id":"mgu-render","reason":"x"}]}')
        with patch.object(store.sshcli, 'run', return_value=(0, 'noise\n' + status + '\n', '')) as run:
            got = store.read_installed('169.254.1.9', 'key')
            self.assertEqual(run.call_args.args[2], store.STATUS_CMD)
        self.assertEqual(got, {'hmi': 'ID7', 'firmware': 'S15C-24-07', 'deploy': '0.8.8',
                               'components': {'robertn_store': '0.9.30', 'mgu-deploy': '0.8.8'},
                               'pending_reboot': ['mgu-render']})
        with patch.object(store.sshcli, 'run', return_value=(0, 'absent\n', '')):
            self.assertIsNone(store.read_installed('169.254.1.9', 'key'))
        for result in ((255, '', 'timeout'), (0, 'not json\n', '')):
            with patch.object(store.sshcli, 'run', return_value=result):
                with self.assertRaises(store.StoreError):
                    store.read_installed('169.254.1.9', 'key')

    def test_catalog_download_and_offline_copy(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d, 'catalog.json')
            src.write_text(__import__('json').dumps(self.CATALOG))
            cache = os.path.join(d, 'cache')
            lines = []
            catalog, online = store.fetch_catalog(lines.append, url=src.as_uri(), folder=cache)
            self.assertTrue(online)
            self.assertEqual(catalog['commit'], 'abc')
            catalog, online = store.fetch_catalog(lines.append, url=Path(d, 'missing').as_uri(), folder=cache)
            self.assertFalse(online)
            self.assertIn('Showing the versions from the last check.', lines)
            with self.assertRaises(store.StoreError):
                store.fetch_catalog(lines.append, url=Path(d, 'missing').as_uri(), folder=os.path.join(d, 'empty'))

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'state.json')
            written = store.write_state({'car': {'vin': 'X'}}, path=path)
            self.assertIn('checked', written)
            self.assertEqual(store.read_state(path)['car'], {'vin': 'X'})
            self.assertIsNone(store.read_state(os.path.join(d, 'none.json')))


class RemoveAndHotspotTests(unittest.TestCase):
    def test_uninstall_outcomes(self):
        cases = ((0, ['Removed. Restart the head unit to finish.'], store.EXIT_RESTART_NEEDED),
                 (0, ['Removed: nothing of the Store setup is left on the head unit.'], store.EXIT_DONE),
                 (1, ['Not finished: x is still on the head unit.'], store.EXIT_FAILED))
        for rc, out, expected in cases:
            with patch.object(store.sshcli, 'available', return_value=True), \
                 patch.object(store, 'ensure_installer', return_value='mgu-install'), \
                 patch.object(store, 'run_installer', return_value=(rc, out)) as run:
                self.assertEqual(store.uninstall('169.254.1.9', 'key', lambda *_: None), expected)
                self.assertEqual(run.call_args.kwargs, {'extra': ('--yes',), 'command': 'uninstall'})

    def test_ssh_banner(self):
        import socket
        import threading
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        port = server.getsockname()[1]

        def serve():
            conn, _ = server.accept()
            conn.sendall(b'SSH-2.0-OpenSSH_8.0\r\n')
            conn.close()
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.assertEqual(store.ssh_banner('127.0.0.1', port), 'SSH-2.0-OpenSSH_8.0')
        t.join(2)
        server.close()
        self.assertIsNone(store.ssh_banner('127.0.0.1', port, timeout=0.5))
