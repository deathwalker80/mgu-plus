"""Offline checks of the MGU Plus window and of the Italian log catalog.
No ENET/SSH calls or vehicle writes."""
import json
from pathlib import Path
import queue
import re
import string
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from i18n import Translator
from mgu7 import store
import gui


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tr = Translator()
        self.tr.select('it')

    def test_only_italian_catalog(self):
        self.assertEqual(sorted(self.tr.catalogs), ['en', 'it'])

    def test_log_keeps_original_only_when_asked(self):
        raw = '[12:34:56] Found gateway 169.254.1.2 | VIN: TESTVIN1234567890\n'
        both = self.tr.log(raw)
        self.assertIn('Trovato il gateway 169.254.1.2 | VIN: TESTVIN1234567890', both)
        self.assertIn('[12:34:56] [EN] Found gateway', both)
        self.assertEqual(self.tr.log(raw, keep_original=False),
                         '[12:34:56] Trovato il gateway 169.254.1.2 | VIN: TESTVIN1234567890\n')
        self.assertEqual(self.tr.log('RAW 31 02 A0 FD\n', keep_original=False), 'RAW 31 02 A0 FD\n')

    def test_catalog_well_formed(self):
        catalog = self.tr.catalogs['it']
        for source, translated in catalog['strings'].items():
            self.assertEqual(re.findall(r'%[sd]', source), re.findall(r'%[sd]', translated), source)
        formatter = string.Formatter()
        sources = [item['source'] for item in catalog['logs']]
        self.assertEqual(len(sources), len(set(sources)))
        for item in catalog['logs']:
            fields = [f for _, f, _, _ in formatter.parse(item['source']) if f is not None]
            targets = [f for _, f, _, _ in formatter.parse(item['translation']) if f is not None]
            self.assertEqual(fields, [str(i) for i in range(len(fields))])
            self.assertEqual(sorted(fields), sorted(targets))
            values = ['VALUE' + f for f in fields]
            rendered = self.tr.log(item['source'].format(*values) + '\n', keep_original=False)
            self.assertEqual(rendered, item['translation'].format(*values) + '\n')

    def test_store_and_check_lines_are_translated(self):
        for raw, expected in [
            ('Store 0.9.30 installed: 3 updates available.', 'Store 0.9.30 installato: 3 aggiornamenti disponibili.'),
            ('Store 0.9.30 installed: 1 update available.', 'Store 0.9.30 installato: 1 aggiornamento disponibile.'),
            ('Latest Store version: 0.9.33', 'Ultima versione dello Store: 0.9.33'),
            ('Root SSH OK on 169.254.199.99.', 'Root SSH OK su 169.254.199.99.'),
            ("Using the car's Wi-Fi hotspot: head unit at 172.16.222.97.",
             'Uso l’hotspot Wi-Fi dell’auto: head unit su 172.16.222.97.'),
            ('DONE: the Store is removed from the head unit.', 'FATTO: lo Store è stato rimosso dalla head unit.'),
            ("The head unit's coding is missing: it holds default values. This often happens after enabling SSH "
             "or flashing. Restore the head unit's coding from a backup (BimmerCode, E-Sys or ISTA) or have HU_MGU "
             "coded to the car, restart the head unit, then run the install again.",
             'Manca la codifica della head unit (contiene i valori di default).'),
        ]:
            self.assertIn(expected, self.tr.log(raw + '\n', keep_original=False))

    def test_fragmented_prints(self):
        q = queue.Queue()
        writer = gui._QueueWriter(q)
        writer.write('SSH OK as root:')
        writer.write(' ')
        writer.write('uid=0(root)')
        self.assertTrue(q.empty())
        writer.write('\npartial')
        self.assertEqual(q.get_nowait(), 'SSH OK as root: uid=0(root)\n')
        writer.flush()
        self.assertEqual(q.get_nowait(), 'partial')


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root = gui.tk.Tk()
        self.root.withdraw()
        self.app = gui.App(self.root, auto_check=False)

    def tearDown(self):
        self.root.destroy()

    def sample_state(self):
        catalog = {'components': [
            {'id': 'mgu-deploy', 'kind': 'tool', 'title': 'Deploy tool', 'version': '0.8.8',
             'texts': {'it': {'title': 'Strumento di distribuzione'}}},
            {'id': 'robertn_store', 'kind': 'app', 'title': 'Store', 'version': '0.9.33', 'mguHmi': ['ID7', 'ID8']},
        ]}
        rows = store.catalog_rows(catalog)
        installed = {'hmi': 'ID7', 'components': {'mgu-deploy': '0.8.8', 'robertn_store': '0.9.30',
                                                  'other_app': '1.0.0'}}
        compared = store.compare(rows, installed)
        return {'car': {'via': 'enet', 'vin': 'TESTVIN1234567890', 'mgu_ip': '169.254.199.99',
                        'serial': '0000123456789', 'hardware': 'HWEL 00003F2A_030.016.002, HWEL 0000ABCD_001.000.000'},
                'root': {'active': True}, 'catalog': {'online': True, 'rows': rows},
                'store': {'installed': installed, 'rows': compared, 'summary': store.summarize(compared, installed)}}

    def test_italian_window_with_version_in_title(self):
        self.assertEqual(self.root.title(), 'MGU Plus %s' % gui.APP_VERSION)
        self.assertEqual(self.app.btn_check.cget('text'), 'Connetti vettura')
        self.assertEqual(self.app.btn_flash.cget('text'), 'Flash + Apri SSH')
        self.assertEqual(self.app.btn_store.cget('text'), 'Installa / Aggiorna')
        self.assertEqual(self.app.card_car.chip.cget('text'), 'NON COLLEGATA')
        self.assertEqual(self.app.variant_label.get(), gui.VARIANT_PROMPT)

    def test_state_fills_cards_and_table(self):
        self.app.state = self.sample_state()
        self.app._refresh()
        self.assertEqual(self.app.card_car.chip.cget('text'), 'COLLEGATA · ENET')
        self.assertEqual(self.app.link.cget('text'), 'ENET · MGU 169.254.199.99')
        self.assertEqual(self.app.card_ssh.chip.cget('text'), 'ROOT ATTIVO')
        self.assertEqual(self.app.card_store.chip.cget('text'), '1 AGGIORNAMENTO')
        self.assertEqual(self.app.car_serial.cget('text'), '0000123456789')
        self.assertEqual(self.app.car_hardware.cget('text'), '00003F2A_030.016.002')
        rows = [self.app.tree.item(i, 'values') for i in self.app.tree.get_children()]
        self.assertEqual(rows, [('Strumento di distribuzione', '0.8.8', '0.8.8', 'Aggiornato'),
                                ('Store', '0.9.30', '0.9.33', 'Aggiornamento disponibile'),
                                ('other_app', '1.0.0', '—', 'Installato (altra fonte)')])
        self.assertEqual(self.app.store_line.cget('text'), 'Store 0.9.30 installato: 1 aggiornamento disponibile.')

    def test_check_result_fills_fields_and_hotspot(self):
        with tempfile.TemporaryDirectory() as d, patch.object(store, 'STATE_FILE', str(Path(d, 'state.json'))):
            state = self.sample_state()
            state['car'] = {'via': 'hotspot', 'vin': None, 'mgu_ip': '172.16.222.97'}
            state['root'] = {'active': False, 'reason': 'login-refused'}
            state['store'] = None
            store.write_state(state)
            self.app._check_done(0)
        self.assertEqual(self.app.gateway.get(), 'hotspot')
        self.assertEqual(self.app.mgu_ip.get(), '172.16.222.97')
        self.assertEqual(self.app.card_car.chip.cget('text'), 'COLLEGATA · HOTSPOT')
        self.assertEqual(self.app.card_ssh.chip.cget('text'), 'CHIAVE RIFIUTATA')
        self.assertEqual(self.app.card_store.chip.cget('text'), 'DA VERIFICARE')
        self.assertIn('Ultimo Store pubblicato: 0.9.33', self.app.store_line.cget('text'))

    def test_buttons_send_the_right_actions(self):
        app = self.app
        app.vin.set('TESTVIN1234567890')
        with patch.object(app, '_run') as run, patch.object(gui.messagebox, 'askyesno', return_value=True), \
                patch.object(gui.messagebox, 'askyesnocancel', return_value=False), \
                patch.object(gui.messagebox, 'showerror') as error:
            app.do_check()
            self.assertEqual(run.call_args.args[0], ['check', '--yes', '--vin', 'TESTVIN1234567890', '--gateway',
                                                     'auto'])
            app.do_enable()
            self.assertEqual(run.call_args.args[0][:4], ['enable', '--persist', '--no-unique-key', '--yes'])
            run.reset_mock()
            app.do_flash()  # tipo MGU non scelto: rifiutato
            run.assert_not_called()
            error.assert_called_once()
            app.variant_label.set(gui.VARIANT_CHOICES[0][1])
            app._on_variant(app.variant_label.get())
            app.do_flash()
            self.assertEqual(run.call_args.args[0][0], 'flash')
            self.assertEqual(run.call_args.args[0][-2:], ['--arch', 'id7'])
            for action, name in ((app.do_backup, 'preflight'), (app.do_store, 'store'), (app.do_remove, 'store-remove'),
                                 (app.do_restart, 'store-restart'), (app.do_disable, 'disable'),
                                 (app.do_fix_key, 'fix-key')):
                action()
                self.assertEqual(run.call_args.args[0][0], name)

    def test_store_and_remove_outcomes(self):
        app = self.app
        with patch.object(app, '_run') as run, patch.object(gui.messagebox, 'askyesno', return_value=True):
            app._store_done(store.EXIT_RESTART_NEEDED)
            self.assertEqual(run.call_args.args[0][0], 'store-restart')
            app._remove_done(store.EXIT_RESTART_NEEDED)
            self.assertEqual(run.call_args.args[0][0], 'store-restart')
        with patch.object(app, '_run') as run, patch.object(gui.messagebox, 'askyesno', return_value=False):
            app._store_done(store.EXIT_RESTART_NEEDED)
            self.assertEqual(run.call_args.args[0][0], 'check')
        for rc, box in ((store.EXIT_DONE, 'showinfo'), (store.EXIT_REFUSED, 'showwarning'),
                        (store.EXIT_FAILED, 'showerror')):
            with patch.object(app, '_run') as run, patch.object(gui.messagebox, box) as shown:
                app._store_done(rc)
                shown.assert_called_once()
                self.assertEqual(run.call_args.args[0][0], 'check')

    def test_busy_disables_every_action_and_clear_log(self):
        self.app._append('Checking debug/SSH state...\n')
        self.assertIn('Controllo dello stato debug/SSH...', self.app.out.get('1.0', 'end'))
        self.assertNotIn('[EN]', self.app.out.get('1.0', 'end'))
        self.app._busy(True)
        for button in self.app._action_buttons():
            self.assertIn('disabled', button.state())
        with patch.object(gui.ssh_enable, 'main') as main:
            self.app._run(['check'], 'check')
            main.assert_not_called()
        self.app._busy(False)
        for button in self.app._action_buttons():
            self.assertNotIn('disabled', button.state())
        self.app.clear_log()
        self.assertEqual(self.app.out.get('1.0', 'end').strip(), '')
        self.assertEqual(self.app._history, [])


class CodingRestoreGuiTests(unittest.TestCase):
    def setUp(self):
        self.root = gui.tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.here = patch.object(gui, 'HERE', self.tmp.name)
        self.here.start()
        self.app = gui.App(self.root, auto_check=False)
        self.app.vin.set('WBA7U2C08RCN12345')
        self.app.variant_label.set(gui.VARIANT_CHOICES[0][1])
        self.app._on_variant(self.app.variant_label.get())

    def tearDown(self):
        self.here.stop()
        self.root.destroy()
        self.tmp.cleanup()

    def add_backup(self, verified=True):
        folder = Path(self.tmp.name, 'data', 'preflight', '20261109-WBA7U2C08RCN12345')
        folder.mkdir(parents=True)
        (folder / 'backup.json').write_text(json.dumps({'vin': 'WBA7U2C08RCN12345', 'taken': '2026-11-09 10:00:00',
                                                         'coding': {'3000': '01'}, 'verified': verified}))

    def test_flash_offers_the_backup_first(self):
        with patch.object(self.app, '_run') as run, patch.object(gui.messagebox, 'askyesno', return_value=True), \
                patch.object(gui.messagebox, 'askyesnocancel', return_value=True):
            self.app.do_flash()
            self.assertEqual(run.call_args.args[0][0], 'preflight')
            with patch.object(gui.messagebox, 'showerror') as error:
                run.call_args.kwargs['on_done'](1)  # backup failed: no flash
            error.assert_called_once()
            self.assertEqual(run.call_count, 1)
        with patch.object(self.app, '_run') as run, patch.object(gui.messagebox, 'askyesno', return_value=True), \
                patch.object(gui.messagebox, 'askyesnocancel', return_value=None):
            self.app.do_flash()  # Annulla
            run.assert_not_called()

    def test_with_a_backup_flash_starts_and_restore_is_offered(self):
        self.add_backup()
        self.app._refresh()
        self.assertIn('backup del 2026-11-09 10:00:00 UTC, 1 blocchi', self.app.coding_info.cget('text'))
        with patch.object(self.app, '_run') as run, patch.object(gui.messagebox, 'askyesno', return_value=True):
            self.app.do_flash()
            self.assertEqual(run.call_args.args[0][0], 'flash')
            self.app._flash_done(0)
            self.assertEqual(run.call_args.args[0][0], 'coding-restore')
            self.app.do_restore()
            self.assertEqual(run.call_args.args[0][0], 'coding-restore')
        for rc, box in ((0, 'showinfo'), (gui.ssh_enable.EXIT_CODING_PARTIAL, 'showwarning'), (1, 'showerror')):
            with patch.object(self.app, '_run') as run, patch.object(gui.messagebox, box) as shown:
                self.app._restore_done(rc)
                shown.assert_called_once()
                self.assertEqual(run.call_args.args[0][0], 'check')

    def test_restore_needs_a_verified_backup(self):
        self.add_backup(verified=False)
        with patch.object(self.app, '_run') as run, patch.object(gui.messagebox, 'showerror') as error:
            self.app.do_restore()
            error.assert_called_once()
            run.assert_not_called()


class StoreLatestTests(unittest.TestCase):
    """“Controlla aggiornamenti GitHub”: le ultime versioni di mgu-store."""

    def setUp(self):
        self.root = gui.tk.Tk()
        self.root.withdraw()
        self.app = gui.App(self.root, auto_check=False)
        self.rows = store.catalog_rows({'components': [
            {'id': 'mgu-deploy', 'kind': 'tool', 'title': 'Deploy tool', 'version': '0.8.9'},
            {'id': 'robertn_store', 'kind': 'app', 'title': 'Store', 'version': '0.9.40'}]})

    def tearDown(self):
        self.root.destroy()

    def test_without_car_shows_latest_versions(self):
        with patch.object(gui.messagebox, 'showinfo') as info:
            self.app._store_latest_done(self.rows, True, 'abc', None)
        self.assertIn('0.9.40', info.call_args.args[1])
        self.assertEqual([self.app.tree.item(i, 'values')[2] for i in self.app.tree.get_children()],
                         ['0.8.9', '0.9.40'])
        self.assertIn('Ultimo Store pubblicato: 0.9.40', self.app.store_line.cget('text'))

    def test_compares_with_the_last_read_head_unit(self):
        installed = {'hmi': 'ID7', 'components': {'mgu-deploy': '0.8.9', 'robertn_store': '0.9.33'}}
        old = store.compare(store.catalog_rows({'components': [
            {'id': 'mgu-deploy', 'version': '0.8.9'}, {'id': 'robertn_store', 'version': '0.9.33'}]}), installed)
        self.app.state = {'car': {'via': 'enet', 'mgu_ip': '169.254.199.99'}, 'root': {'active': True},
                          'store': {'installed': installed, 'rows': old, 'summary': store.summarize(old, installed)}}
        self.app._refresh()
        self.assertEqual(self.app.card_store.chip.cget('text'), 'AGGIORNATO')
        with patch.object(gui.messagebox, 'showinfo') as info:
            self.app._store_latest_done(self.rows, True, 'abc', None)
        self.assertIn('Componenti da aggiornare o installare: 1', info.call_args.args[1])
        self.assertEqual(self.app.card_store.chip.cget('text'), '1 AGGIORNAMENTO')

    def test_failure_is_reported(self):
        with patch.object(gui.messagebox, 'showwarning') as warn:
            self.app._store_latest_done(None, False, None, 'offline')
        self.assertIn('offline', warn.call_args.args[1])

    def test_button_runs_in_background(self):
        catalog = {'commit': 'abc', 'components': [{'id': 'robertn_store', 'version': '0.9.40'}]}
        with patch.object(store, 'fetch_catalog', return_value=(catalog, True)), \
                patch.object(gui.messagebox, 'showinfo'):
            self.app.do_store_latest()
            self.assertIn('disabled', self.app.btn_github.state())
            self.app.worker.join(5)
            for _ in range(50):  # the worker hands the result back through root.after
                self.root.update()
                if not self.app._is_busy:
                    break
                gui.time.sleep(0.05)
        self.assertNotIn('disabled', self.app.btn_github.state())


class StartupTests(unittest.TestCase):
    def test_latest_store_versions_are_read_at_startup_quietly(self):
        root = gui.tk.Tk()
        root.withdraw()
        try:
            catalog = {'commit': 'abc', 'components': [{'id': 'robertn_store', 'title': 'Store', 'version': '0.9.40'}]}
            with patch.object(store, 'fetch_catalog', return_value=(catalog, True)), \
                    patch.object(gui.messagebox, 'showinfo') as info:
                app = gui.App(root)
                for _ in range(80):
                    root.update()
                    if app.tree.get_children() and not app._is_busy:
                        break
                    gui.time.sleep(0.05)
                info.assert_not_called()
            rows = [app.tree.item(i, 'values') for i in app.tree.get_children()]
            self.assertEqual(rows, [('Store', '—', '0.9.40', 'Da confrontare con la MGU')])
            self.assertIn('Ultimo Store pubblicato: 0.9.40', app.store_line.cget('text'))
        finally:
            root.destroy()


class ProgramUpdateTests(unittest.TestCase):
    def fake(self, payload=None, error=None):
        class Response:
            def __init__(self, data):
                self.data = data

            def read(self):
                return json.dumps(self.data).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(request, timeout=None):
            self.assertEqual(request.full_url, gui.UPDATE_API)
            if error:
                raise error
            return Response(payload)
        return patch.object(gui.urllib.request, 'urlopen', urlopen)

    def test_outcomes(self):
        self.assertEqual(gui.UPDATE_API, 'https://api.github.com/repos/deathwalker80/mgu-plus/releases/latest')
        with self.fake({'tag_name': 'v1.4.0', 'html_url': 'https://example/r'}):
            self.assertEqual(gui.check_program_update(current='1.3.0'), ('newer', '1.4.0', 'https://example/r', None))
        with self.fake({'tag_name': 'v1.3.0', 'html_url': 'https://example/r'}):
            self.assertEqual(gui.check_program_update(current='1.3.0')[0], 'current')
        with self.fake(error=gui.urllib.error.HTTPError('u', 404, 'Not Found', {}, None)):
            self.assertEqual(gui.check_program_update()[0], 'none')
        with self.fake(error=gui.urllib.error.URLError('offline')):
            self.assertEqual(gui.check_program_update(), ('error', None, None, 'offline'))


if __name__ == '__main__':
    unittest.main()
