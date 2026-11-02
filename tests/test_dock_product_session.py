"""Real file-operation tests; the USB transport is an explicit PC-only fake."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import dock as app
from dock_product_session import DockProductSession
from retro_runtime import private_save_paths
from psp_retro import SYSTEMS


class FakeTransport:
    capabilities = {'automatic_transition': True, 'controller_during_storage': True}

    def __init__(self, root):
        self.root = Path(root)
        self.serial = app.volume_identity(root)
        self.session_id = '11' * 16
        self.phase = 1
        self.connected = True
        self.exit_requested = False
        self.veto = False
        self.roles = []
        self.finishes = 0
        self.begins = 0
        self.host_confirmed = False
        self.progress = []
        self.connection = {'phase':'waiting', 'message':'Synthetic waiting', 'hint':'', 'diagnostics':'', 'retryable':False}

    @property
    def current(self):
        if not self.connected:
            return None
        return {'session_id': self.session_id, 'volume_serial': self.serial,
                'storage_ready': self.phase in (1, 3, 4), 'controller_ready': self.phase == 2 and self.host_confirmed,
                'safe_ready': self.phase == 5, 'controller_available': self.phase in (1, 2) and self.host_confirmed,
                'phase': self.phase, 'path': 'synthetic-native-hid', 'product_id': 0x01CD,
                'revision': 0x0100, 'usb_revision': 0x0100, 'protocol_version':3,
                'firmware_revision':0x0303, 'host_confirmed': self.host_confirmed,
                'capabilities':0x0007, 'build_id':0x03030001,
                'usb_parent': 'USB\\VID_054C&PID_01CD\\SYNTHETIC',
                'root': str(self.root), 'exit_requested': self.exit_requested}

    def observe(self, devices):
        self.connection.update(phase='verified' if self.connected else 'waiting')
        return self.current

    def connection_status(self):
        return dict(self.connection)

    def connection_error(self, exc):
        self.connection.update(phase='blocked', message='Synthetic connection failed', diagnostics=str(exc))

    def begin_session(self, context):
        if context['identity'] != {'session_id': self.session_id, 'volume_serial': self.serial} or self.phase != 1:
            raise ValueError('Synthetic handshake mismatch')
        self.begins += 1
        self.host_confirmed = True
        return {'accepted':True}

    def set_progress(self, context, activity, **fields):
        self.progress.append({'activity':activity, **fields})
        return {'accepted':True, 'exit_requested':self.exit_requested}

    def assert_storage(self, root):
        if not self.connected or not self.host_confirmed or self.phase not in (1, 3, 4) or Path(root) != self.root:
            raise ValueError('Synthetic storage identity/phase mismatch')
        return SimpleNamespace(volume_serial=self.serial)

    def assert_controller(self, expected_session=None):
        if not self.connected or not self.host_confirmed or self.phase != 2 or expected_session not in (None, self.session_id):
            raise ValueError('Synthetic controller identity/phase mismatch')
        return self.current

    def request_role(self, context):
        if not self.host_confirmed or context['identity'] != {'session_id': self.session_id, 'volume_serial': self.serial}:
            raise ValueError('Synthetic role identity mismatch')
        self.roles.append((context['role'], context['purpose']))
        self.phase = 2 if context['role'] == 'controller' else {
            'initial': 1, 'game_copy': 3, 'exit': 4}[context['purpose']]
        return {'accepted': True}

    def controller_status(self):
        return {'connected': self.connected and self.phase in (1, 2) and self.host_confirmed, 'can_play': self.phase == 2 and self.host_confirmed,
                'mode': 'native_dock', 'synthetic_storage': False, 'path': 'synthetic-native-hid',
                'session': self.session_id, 'phase': self.phase, 'product_id': 0x01CD,
                'revision': 0x0100, 'usb_revision':0x0100, 'protocol_version':3,
                'firmware_revision':0x0303, 'host_confirmed':self.host_confirmed,
                'capabilities':0x0007, 'build_id':0x03030001,
                'reason': 'Synthetic transport for tests'}

    def finish_session(self, *, cancel=False):
        self.finishes += 1
        if cancel and not self.host_confirmed and self.phase == 1:
            if self.veto:
                raise ValueError('Synthetic Windows safe-eject veto')
            self.connected = False
            return {'verified':True, 'host_eject_accepted':True, 'entry_unclaimed':True}
        if cancel and self.host_confirmed and self.phase in (1, 2):
            self.phase = 4
        if self.phase not in (4, 5):
            raise ValueError('Synthetic finish in wrong phase')
        self.phase = 5
        if self.veto:
            raise ValueError('Synthetic Windows safe-eject veto')
        self.connected = False
        return {'verified': True, 'host_eject_accepted': True}


class DockProductSessionTests(unittest.TestCase):
    def setUp(self):
        self.no_usb = patch.dict(app.os.environ, {'PSP_DOCK_TEST_NO_REAL_USB': '1'})
        self.no_usb.start()
        self.addCleanup(self.no_usb.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.psp = self.home/'synthetic-psp'
        (self.psp/'PSP/SAVEDATA').mkdir(parents=True)
        (self.psp/'ISO').mkdir()
        (self.psp/'ISO/Game.iso').write_bytes(b'synthetic game content')
        self.dock = app.Dock(self.home/'data')
        # Ignore a real bundled PPSSPP in the checkout. These tests exercise
        # session/file orchestration; launch cases supply their own fake exe.
        # Persist this choice so recovery fixtures inherit the same isolation.
        self.dock.settings({'psp_root':str(self.psp), 'emulator':''})
        self.transport = FakeTransport(self.psp)
        self.product = DockProductSession(self.dock, self.transport)
        self.dock.product_session = self.product
        self.dock.product_flow = self.product.flow
        self.dock.native_transport = self.transport
        self.pc_saves = Path(self.dock.config['memstick'])/'PSP/SAVEDATA'

    def slot(self, root, name, value):
        target = root/name/'DATA.BIN'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(value)
        return target

    def retro_fixture(self):
        base = self.psp/'PSP/GAME/gpSP'
        (base/'ROMS').mkdir(parents=True)
        (base/'SAVES').mkdir()
        (base/'EBOOT.PBP').write_bytes(b'synthetic emulator, never execute')
        (base/'ROMS/Golden.gba').write_bytes(b'synthetic ROM, never execute')
        save = base/'SAVES/Golden.sav'
        save.write_bytes(b'a'*32768)
        state = base/'SAVES/Golden.svs'
        state.write_bytes(b'PSP savestate, private emulator format')
        return save, state

    def mock_windows(self):
        # Replace Dock's namespace only, not os.name globally: pathlib
        # must continue using the actual host platform in these file tests.
        return patch.object(app, 'os', SimpleNamespace(**{**vars(app.os), 'name':'nt'}))

    def watch_once(self):
        self.dock.stop.clear()
        with patch.object(self.dock.stop, 'wait', side_effect=lambda seconds:self.dock.stop.set()):
            self.dock.watch()
        self.dock.stop.clear()  # The fixture represents a live app between single worker turns.

    def tick(self):
        result = self.product.tick(self.dock.devices())
        self.dock.snapshot = {**self.dock.state(), 'scanning':False}
        return result

    def offer(self):
        self.tick()
        state = self.product.snapshot()
        self.assertEqual(state['phase'], 'choosing')
        return state['offer_id']

    def ready(self, choice='none'):
        token = self.offer()
        self.product.choose(choice, token)
        for _ in range(5):
            self.tick()
            if self.product.snapshot()['phase'] == 'ready':
                return
        self.fail(str(self.product.snapshot()))

    def exit(self):
        self.dock.request_exit()
        for _ in range(6):
            self.tick()
            if self.product.snapshot()['phase'] in ('safe_to_disconnect','attention'):
                return self.product.snapshot()
        self.fail(str(self.product.snapshot()))

    def test_handshake_is_journaled_before_it_runs_and_snapshot_never_claims(self):
        for _ in range(3):
            self.product.snapshot()
        self.assertEqual(self.transport.begins, 0)
        begin = self.transport.begin_session
        def checked(context):
            journal = app.read_json(self.product.flow.path, {})
            self.assertEqual(journal['operation']['name'], 'inventory')
            self.assertEqual(journal['operation']['status'], 'started')
            self.assertEqual(journal['operation']['id'], context['operation_id'])
            self.assertFalse(self.product.entry_path.exists())
            return begin(context)
        with patch.object(self.transport, 'begin_session', side_effect=checked):
            self.offer()
        self.assertEqual(self.transport.begins, 1)

    def test_publishing_native_inventory_does_not_repeat_game_and_rom_scan(self):
        self.offer()
        with patch.object(self.dock, 'games', side_effect=AssertionError('duplicate PSP game scan')), \
             patch.object(self.dock, 'retro_catalog', side_effect=AssertionError('duplicate PSP ROM scan')):
            view = self.dock.state(cached_catalogs=True)
        self.assertEqual(view['games'], self.dock.cached_game_results)
        self.assertEqual(view['retro'], self.dock.cached_retro_results)
        self.assertFalse(view['can_launch_game'])
        self.assertEqual(view['product_flow']['phase'], 'choosing')

    def test_failed_host_begin_publishes_attention_without_any_catalog_scan(self):
        self.assertIsNone(self.dock.cached_game_results)
        self.assertIsNone(self.dock.cached_retro_results)
        with patch.object(self.transport, 'begin_session', side_effect=ValueError('HOST_BEGIN sin ACK')) as begin, \
             patch.object(self.transport, 'observe', wraps=self.transport.observe) as observe, \
             patch.object(self.dock, 'state', side_effect=AssertionError('no fallback after failed handshake')) as state, \
             patch.object(self.dock, 'games', side_effect=AssertionError('no PC game inventory')) as games, \
             patch.object(self.dock, 'retro_catalog', side_effect=AssertionError('no PC ROM inventory')) as retro:
            for _ in range(3):
                self.watch_once()

        begin.assert_called_once()
        self.assertEqual(observe.call_count, 3)
        state.assert_not_called()
        games.assert_not_called()
        retro.assert_not_called()
        view = self.dock.snapshot
        self.assertEqual(view['product_flow']['phase'], 'attention')
        self.assertEqual(view['product_flow']['purpose'], 'initial')
        self.assertIn('HOST_BEGIN sin ACK', view['product_flow']['attention_reason'])
        self.assertFalse(view['product_flow']['entry_completed'])
        self.assertFalse(self.transport.host_confirmed)
        self.assertIsNone(view['selected_device'])
        self.assertFalse(view['scanning'])
        self.assertFalse(view['can_launch_game'])
        self.assertFalse(view['can_launch_retro'])
        self.assertFalse(view['controller_status']['connected'])
        stages = self.product.diagnostics.snapshot()['stages']
        self.assertEqual(stages['ui.publish_native_waiting']['count'], 3)
        self.assertNotIn('ui.publish', stages)

    def test_native_publication_rebuilds_invalidated_catalogs_instead_of_old_snapshot(self):
        self.offer()
        games = list(self.dock.cached_game_results)
        retro = {'emulators':[], 'roms':[{'availability':'ready', 'core_available':True}]}
        self.dock.snapshot['retro'] = {'emulators':[], 'roms':[{'availability':'ready', 'core_available':False}]}
        self.dock.cached_game_results = self.dock.cached_retro_results = None
        with patch.object(self.dock, 'games', return_value=games) as refresh_games, \
             patch.object(self.dock, 'retro_catalog', return_value=retro) as refresh_retro:
            view = self.dock.state(cached_catalogs=True)
        refresh_games.assert_called_with()
        refresh_retro.assert_called_with()
        self.assertEqual(view['games'], games)
        self.assertTrue(view['retro']['roms'][0]['core_available'])

    def test_psp_exit_before_inventory_ejects_without_claim_or_copies(self):
        original = self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'psp only')
        self.dock.session['pending_return'] = True  # Preserve unrelated recovery metadata.
        self.transport.exit_requested = True
        with patch('dock.copy_slot', side_effect=AssertionError('unexpected save write')), \
             patch.object(self.dock, 'transfer', side_effect=AssertionError('unexpected game copy')):
            self.tick()
        self.assertEqual(self.product.snapshot()['purpose'], 'cancel')
        self.assertEqual(self.product.snapshot()['phase'], 'safe_to_disconnect')
        self.assertEqual(self.transport.begins, 0)
        self.assertEqual(self.transport.finishes, 1)
        self.assertEqual(self.transport.roles, [])
        self.assertFalse(self.product.entry_path.exists())
        self.assertEqual(original.read_bytes(), b'psp only')
        self.assertTrue(self.dock.session['pending_return'])

    def test_cancel_after_failed_begin_never_repeats_the_failed_command(self):
        saved = self.slot(self.psp/'PSP/SAVEDATA', 'SAVE', b'original')
        with patch.object(self.transport, 'begin_session', side_effect=ValueError('missing HOST_BEGIN ACK')) as begin, \
             patch('dock.copy_slot', side_effect=AssertionError('cancel must not copy saves')):
            self.tick()
            self.assertEqual(self.product.snapshot()['phase'], 'attention')
            self.assertTrue(self.product.snapshot()['can_cancel'])
            self.product.request_cancel()
            self.tick()
            self.assertEqual(begin.call_count, 1)
        self.assertEqual(self.product.snapshot()['phase'], 'safe_to_disconnect')
        self.assertEqual(self.transport.finishes, 1)
        self.assertFalse(self.product.entry_path.exists())
        self.assertEqual(saved.read_bytes(), b'original')

    def test_connection_timing_distinguishes_observation_and_failed_handshake(self):
        with patch.object(self.transport, 'begin_session', side_effect=ValueError('missing ACK')):
            self.tick()
        timing = self.product.diagnostics.snapshot()
        self.assertEqual(timing['stages']['usb.observe']['count'], 1)
        self.assertEqual(timing['stages']['usb.host_begin']['errors'], 1)
        self.assertEqual(timing['stages']['callback.inventory']['errors'], 1)
        durable = app.read_json(self.dock.data/'connection-timing.json', {})
        self.assertEqual(durable['events'][-1]['event'], 'error')

    def test_usb_discovery_is_visible_before_windows_finishes_enumerating(self):
        with self.product.diagnostics.measure('usb.devices'):
            with patch.object(self.transport, 'observe', side_effect=AssertionError('snapshot must not probe')):
                view = self.product.snapshot()
            self.assertEqual(view['connection']['phase'], 'searching')
            self.assertFalse(view['can_choose'])
        self.assertEqual(self.product.snapshot()['connection']['phase'], 'waiting')

    def test_psp_exit_drains_current_save_and_never_starts_the_next_copy(self):
        self.slot(self.psp/'PSP/SAVEDATA','SAVE_A',b'first')
        self.slot(self.psp/'PSP/SAVEDATA','SAVE_B',b'second')
        self.product.choose('saves', self.offer())
        copy = app.copy_slot
        copied = []
        def finish_then_exit(*args, **kwargs):
            result = copy(*args, **kwargs)
            copied.append(args[1])
            self.transport.exit_requested = True
            return result
        with patch('dock.copy_slot', side_effect=finish_then_exit):
            self.tick()
        self.assertEqual(len(copied), 1)
        self.assertTrue((copied[0]/'DATA.BIN').is_file())
        self.assertEqual(len(list(self.pc_saves.glob('*/DATA.BIN'))), 1)
        self.assertEqual(self.product.snapshot()['phase'], 'finalizing')
        self.assertEqual(self.product.snapshot()['purpose'], 'cancel')
        with patch('dock.copy_slot', side_effect=AssertionError('copy replayed on cancel')):
            self.tick()
        self.assertEqual(self.product.snapshot()['phase'], 'safe_to_disconnect')
        self.assertEqual(len(list((self.psp/'PSP/SAVEDATA').glob('*/DATA.BIN'))), 2)

    def test_runtime_and_enumeration_timeout_are_published_without_killing_worker(self):
        for error in (RuntimeError('HID enumeration failed'), app.subprocess.TimeoutExpired('CIM', 30)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(self.transport, 'observe', side_effect=error):
                self.watch_once()
            view = self.product.snapshot()
            self.assertEqual(view['connection']['phase'], 'blocked')
            self.assertIn(str(error), view['connection']['diagnostics'])
            self.assertFalse(self.dock.snapshot['scanning'])
            self.assertEqual(self.transport.begins, 0)
        self.watch_once()
        self.assertEqual(self.product.snapshot()['phase'], 'choosing')

    def test_psp_exit_with_open_emulator_shows_waiting_and_never_starts_sync(self):
        self.ready('none')
        process = Mock()
        process.poll.return_value = None
        self.dock.process = process
        self.dock.session.update(running=True, pending_return=True)
        self.product.flow.set_game_running(True)
        self.transport.exit_requested = True
        before = list(self.transport.roles)
        self.product.tick(self.dock.devices())
        self.assertEqual(self.product.snapshot()['phase'], 'waiting_game_exit')
        self.assertIn('Cierra el emulador', self.product.snapshot()['message'])
        self.assertEqual(self.transport.roles, before)
        self.assertEqual(self.transport.finishes, 0)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_inventory_and_verified_save_progress_are_live_and_use_real_totals(self):
        self.retro_fixture()
        self.slot(self.psp/'PSP/SAVEDATA','SAVE_A',b'first')
        self.slot(self.psp/'PSP/SAVEDATA','SAVE_B',b'second')
        views = []
        progress = self.transport.set_progress
        def capture(context, activity, **fields):
            view = self.product.snapshot()
            value = view['progress']
            self.assertTrue(value['active'])
            self.assertEqual(value['operation_id'], context['operation_id'])
            views.append(dict(value))
            return progress(context, activity, **fields)
        with patch.object(self.transport, 'set_progress', side_effect=capture):
            self.offer()
            self.product.choose('saves', self.product.snapshot()['offer_id'])
            self.tick()
        inventory = [row for row in views if row['stage'] == 'inventory']
        self.assertTrue(any(row['completed'] > 0 for row in inventory))
        self.assertTrue(all(row['mode'] == 'indeterminate' and row['total'] is None for row in inventory))
        saved = [row for row in views if row['stage'] == 'saves' and row['mode'] == 'determinate']
        self.assertTrue(saved)
        self.assertTrue(any(row['completed'] == row['total'] == 3 for row in saved))
        self.assertEqual((self.pc_saves/'SAVE_A/DATA.BIN').read_bytes(), b'first')
        self.assertEqual((self.pc_saves/'SAVE_B/DATA.BIN').read_bytes(), b'second')

    def test_game_byte_progress_is_bound_to_operation_and_verification_has_no_fake_percent(self):
        self.dock.transfer_status.update(active=True, copied_bytes=99, total_bytes=100)
        self.assertEqual(self.product.snapshot()['progress'], {'active': False})
        self.product.choose('games', self.offer())
        views = []
        publish = self.product.publish_progress
        def capture(*args, **kwargs):
            result = publish(*args, **kwargs)
            views.append(self.product.snapshot()['progress'])
            return result
        with patch.object(self.product, 'publish_progress', side_effect=capture):
            self.tick()
        byte_rows = [r for r in views if r.get('stage') == 'games' and r.get('mode') == 'determinate']
        self.assertTrue(byte_rows)
        expected = (self.psp/'ISO/Game.iso').stat().st_size
        self.assertTrue(all(row['total'] == expected for row in byte_rows))
        self.assertTrue(any(row['completed'] == expected for row in byte_rows))
        self.assertTrue(all(r['total'] is None for r in views if r.get('stage') == 'verifying'))
        self.assertEqual(len({r['operation_id'] for r in views}), 1)

    def test_initial_none_inventories_without_copying_games_or_partidas(self):
        psp_file = self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'psp version')
        with patch.object(self.dock, 'sync', side_effect=AssertionError('implicit save copy')), \
             patch.object(self.dock, 'transfer', side_effect=AssertionError('implicit game copy')):
            self.ready('none')
        self.assertFalse((Path(self.dock.config['library'])/'ISO/Game.iso').exists())
        self.assertFalse((self.pc_saves/'SAVE').exists())
        self.assertEqual(psp_file.read_bytes(),b'psp version')
        self.assertTrue(self.product.snapshot()['use_pc_saves'])
        self.assertNotIn('prepared_volume', self.dock.session)
        self.assertIsNone(self.dock.root())  # Mounted physically, unavailable to product reads in mando.

    def test_all_copies_missing_games_and_saves_before_controller(self):
        self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'psp version')
        self.ready('all')
        self.assertEqual((self.pc_saves/'SAVE/DATA.BIN').read_bytes(), b'psp version')
        self.assertEqual((Path(self.dock.config['library'])/'ISO/Game.iso').read_bytes(), b'synthetic game content')
        self.assertEqual(self.transport.roles, [('controller','initial')])
        self.assertEqual(self.dock.session['prepared_volume'], self.transport.serial)
        self.assertFalse(self.product.snapshot()['use_pc_saves'])

    def test_games_only_keeps_existing_pc_saves_and_records_original_difference(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'psp version')
        pc_file=self.slot(self.pc_saves,'SAVE',b'pc version')
        self.ready('games')
        self.assertTrue((Path(self.dock.config['library'])/'ISO/Game.iso').exists())
        self.assertEqual(psp_file.read_bytes(),b'psp version')
        self.assertEqual(pc_file.read_bytes(),b'pc version')
        self.assertNotEqual(self.product.entry['psp']['SAVE'],self.product.entry['pc']['SAVE'])
        self.assertTrue(self.product.snapshot()['use_pc_saves'])

    def test_saves_only_does_not_copy_missing_game(self):
        self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'psp version')
        self.ready('saves')
        self.assertTrue((self.pc_saves/'SAVE/DATA.BIN').exists())
        self.assertFalse((Path(self.dock.config['library'])/'ISO/Game.iso').exists())

    def test_changed_inventory_requires_new_choice_without_copy(self):
        token=self.offer()
        (self.psp/'ISO/New.iso').write_bytes(b'new game after displayed offer')
        self.product.choose('all',token)
        self.tick()
        self.assertEqual(self.product.snapshot()['phase'],'attention')
        self.assertFalse(Path(self.dock.config['library']).exists())

    def test_copy_later_preserves_entry_baseline_and_pending_return(self):
        self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'old PSP')
        pc_file=self.slot(self.pc_saves,'SAVE',b'old PC')
        self.ready('none')
        old_entry=dict(self.product.entry['pc'])
        pc_file.write_bytes(b'played on PC')
        self.dock.session['pending_return']=True
        self.product.flow.set_game_running(True)
        self.product.flow.set_game_running(False)
        game=next(g for g in self.dock.games() if g['availability']=='missing')
        self.dock.request_game_copy_from_hid(game['id'])
        for _ in range(5):
            self.tick()
            if self.product.snapshot()['phase']=='ready':break
        self.assertEqual(self.product.snapshot()['phase'],'ready')
        self.assertEqual(self.product.entry['pc'],old_entry)
        self.assertTrue(self.dock.session['pending_return'])
        self.assertEqual(self.transport.roles[-2:],[('storage','game_copy'),('controller','game_copy')])

    def test_exit_exports_only_pc_changes_and_never_imports_unrelated_psp_changes(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'old PSP')
        pc_file=self.slot(self.pc_saves,'SAVE',b'old PC')
        other_psp=self.slot(self.psp/'PSP/SAVEDATA','OTHER',b'old other')
        other_pc=self.slot(self.pc_saves,'OTHER',b'old other')
        self.ready('none')
        self.product.flow.set_game_running(True)
        self.product.flow.set_game_running(False)
        pc_file.write_bytes(b'new PC')
        other_psp.write_bytes(b'external PSP change')
        with patch.object(self.dock,'sync',side_effect=AssertionError('bidirectional sync on exit')):
            result=self.exit()
        self.assertEqual(result['phase'],'safe_to_disconnect')
        self.assertEqual(psp_file.read_bytes(),b'new PC')
        self.assertEqual(other_psp.read_bytes(),b'external PSP change')
        self.assertEqual(other_pc.read_bytes(),b'old other')
        self.assertEqual(self.transport.finishes,1)
        self.assertFalse(self.dock.session['pending_return'])
        self.assertEqual(self.dock.session['return_result'],'verified')

    def test_exit_does_not_export_unchanged_pc_save_after_none(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'PSP kept')
        self.slot(self.pc_saves,'SAVE',b'PC kept')
        self.ready('none')
        with patch('dock.copy_slot',side_effect=AssertionError('no session change to export')):
            self.assertEqual(self.exit()['phase'],'safe_to_disconnect')
        self.assertEqual(psp_file.read_bytes(),b'PSP kept')

    def test_exit_conflict_can_keep_psp_without_overwriting_pc(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'old')
        pc_file=self.slot(self.pc_saves,'SAVE',b'old')
        self.ready('none')
        psp_file.write_bytes(b'external PSP')
        pc_file.write_bytes(b'played PC')
        result=self.exit()
        self.assertEqual(result['phase'],'attention')
        self.assertTrue(result['can_review_saves'])
        conflict=self.dock.snapshot['saves']['conflicts'][0]
        self.dock.sync({'slot':conflict['slot'],'choice':'psp','revision':conflict['revision']})
        self.assertEqual(psp_file.read_bytes(),b'external PSP')
        self.assertEqual(pc_file.read_bytes(),b'played PC')
        self.product.retry(result['offer_id'])
        for _ in range(4):self.tick()
        self.assertEqual(self.product.snapshot()['phase'],'safe_to_disconnect')
        self.assertEqual(pc_file.read_bytes(),b'played PC')

    def test_stale_return_conflict_revision_does_not_write(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'old')
        pc_file=self.slot(self.pc_saves,'SAVE',b'old')
        self.ready('none')
        psp_file.write_bytes(b'external PSP')
        pc_file.write_bytes(b'played PC')
        self.exit()
        conflict=self.dock.snapshot['saves']['conflicts'][0]
        psp_file.write_bytes(b'external PSP changed again')
        with self.assertRaisesRegex(ValueError,'cambió'):
            self.dock.sync({'slot':conflict['slot'],'choice':'pc','revision':conflict['revision']})
        self.assertEqual(psp_file.read_bytes(),b'external PSP changed again')

    def test_safe_eject_veto_retries_only_finalization(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'old')
        pc_file=self.slot(self.pc_saves,'SAVE',b'old')
        self.ready('none')
        pc_file.write_bytes(b'new PC')
        self.transport.veto=True
        with patch('dock.copy_slot', wraps=app.copy_slot) as copier:
            result=self.exit()
            self.assertEqual(result['phase'],'attention')
            self.assertEqual(copier.call_count,1)
            self.assertEqual(psp_file.read_bytes(),b'new PC')
            self.assertTrue(self.product.entry['return_data_verified'])
            self.tick()  # Observe terminal SAFE after the veto.
            self.transport.veto=False
            self.product.retry(self.product.snapshot()['offer_id'])
            self.tick()
            self.assertEqual(self.product.snapshot()['phase'],'safe_to_disconnect')
            self.assertEqual(copier.call_count,1)
            self.assertEqual(self.transport.finishes,2)

    def test_pc_only_title_is_present_and_distinguished_from_psp_titles(self):
        local=Path(self.dock.config['library'])/'ISO/Only-PC.iso'
        local.parent.mkdir(parents=True,exist_ok=True)
        local.write_bytes(b'PC-only synthetic game')
        self.offer()
        pc_only=next(g for g in self.dock.games() if g['title']=='Only-PC')
        self.assertFalse(pc_only['on_psp'])
        self.assertTrue(pc_only['on_pc'])
        self.assertEqual(pc_only['availability'],'ready')

    def test_active_native_session_refuses_relocation_before_migration(self):
        self.ready('none')
        before=dict(self.dock.config)
        with patch('storage_layout.migrate_pc_data') as migrate:
            with self.assertRaisesRegex(ValueError,'Termina la sesión del Dock'):
                self.dock.relocate_storage(str(self.home/'new-library'))
        migrate.assert_not_called()
        self.assertEqual(self.dock.config,before)
        self.assertFalse((self.home/'new-library').exists())

    def test_launcher_input_requires_firmware_and_handshake_independent_of_sony_usb_revision(self):
        self.ready('none')
        with patch('psp_launcher_input.PSPWinMMReader') as reader:
            reader.return_value.read.return_value = {'connected': True, 'source': 'psp_winmm'}
            original = dict(self.dock.snapshot['controller_status'])
            for firmware, build in ((0x0303, 0x03030001), (0x0304, 0x03040001)):
                self.dock.snapshot['controller_status'] = {
                    **original, 'firmware_revision':firmware, 'build_id':build}
                self.assertTrue(self.dock.psp_launcher_input()['connected'])
            self.assertEqual(reader.call_count, 2)
            reader.assert_called_with(expected_product=0x01CD)
            for changes in ({'firmware_revision':0x0301}, {'protocol_version':2},
                            {'host_confirmed':False}, {'capabilities':0},
                            {'firmware_revision':0x0304}, {'build_id':0x03040001},
                            {'firmware_revision':[]}, {'firmware_revision':None},
                            {'build_id':None}, {'synthetic_storage':True}):
                self.dock.snapshot['controller_status'] = {**original, **changes}
                self.assertFalse(self.dock.psp_launcher_input()['connected'])
            self.assertEqual(reader.call_count, 2)

    def test_dock_apps_are_excluded_but_unrelated_homebrew_is_preserved(self):
        for name in ('PSPDock','pspdocknative','PSPDockStorageTest','PSPDockCompositeTest',
                     'MyHomebrew','ARK_Loader'):
            target=self.psp/'PSP/GAME'/name/'EBOOT.PBP'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'synthetic homebrew, never execute')
        titles={item['title'] for item in app.game_inventory(self.psp)}
        self.assertEqual(titles,{'Game','MyHomebrew','ARK_Loader'})

    def test_non_game_names_are_filtered_before_file_and_path_metadata_checks(self):
        homebrew=self.psp/'PSP/GAME/Collection/MyHomebrew/EBOOT.PBP'
        homebrew.parent.mkdir(parents=True)
        homebrew.write_bytes(b'synthetic legitimate nested homebrew')
        ignored=[homebrew.parent/'Title.gba',homebrew.parent/'Title.sav',
                 self.psp/'ISO/notes.txt']
        for path in ignored:path.write_bytes(b'not a PSP game candidate')
        visited=[]
        is_file=Path.is_file
        def record_file_metadata(path):
            visited.append(path)
            return is_file(path)
        with patch('dock.inside',wraps=app.inside) as checked, \
             patch.object(Path,'is_file',record_file_metadata):
            games=app.game_inventory(self.psp)
        checked_names={str(call.args[1]) for call in checked.call_args_list}
        self.assertIn('ISO/Game.iso',checked_names)
        self.assertIn('PSP/GAME/Collection/MyHomebrew/EBOOT.PBP',checked_names)
        self.assertIn(homebrew,visited)
        self.assertIn(self.psp/'ISO/Game.iso',visited)
        for path in ignored:
            self.assertNotIn(path,visited)
            self.assertNotIn(path.relative_to(self.psp).as_posix(),checked_names)
        self.assertEqual({game['title'] for game in games},{'Game','MyHomebrew'})

    def test_shutdown_during_enumeration_or_observation_never_starts_callback(self):
        self.ready('none')
        self.dock.request_exit()
        with patch.object(self.transport,'request_role',wraps=self.transport.request_role) as role, \
             patch.object(self.transport,'finish_session',wraps=self.transport.finish_session) as finish:
            def stop_during_devices():
                self.dock.stop.set()
                return []
            with patch.object(self.dock,'devices',side_effect=stop_during_devices), \
                 patch.object(self.product.flow,'advance',wraps=self.product.flow.advance) as advance:
                self.dock.watch()
                advance.assert_not_called()
            self.dock.stop.clear()
            def stop_during_observe(devices):
                self.dock.stop.set()
                return self.transport.current
            with patch.object(self.transport,'observe',side_effect=stop_during_observe), \
                 patch.object(self.product.flow,'advance',wraps=self.product.flow.advance) as advance:
                self.assertFalse(self.product.tick([]))
                advance.assert_not_called()
            role.assert_not_called()
            finish.assert_not_called()
            self.assertEqual(self.product.flow.snapshot()['phase'],'waiting_storage_return')

    def test_callback_admission_rechecks_shutdown_after_flow_intent(self):
        self.ready('none')
        self.dock.request_exit()
        self.dock.stop.set()
        with patch.object(self.transport,'request_role') as role:
            self.product.flow.advance()  # Direct call exercises the last admission gate.
        role.assert_not_called()
        self.assertEqual(self.product.flow.snapshot()['phase'],'attention')
        self.assertIn('cerrando',self.product.flow.snapshot()['attention_reason'])

    def test_shutdown_during_admitted_copy_allows_verification_but_no_next_role(self):
        self.ready('none')
        game=next(g for g in self.dock.games() if g['availability']=='missing')
        self.dock.request_game_copy_from_hid(game['id'])
        self.tick()  # Controller -> COPYING is confirmed by the fake transport.
        copy_game=app.copy_game
        def stop_during_copy(*args,**kwargs):
            self.dock.stop.set()
            return copy_game(*args,**kwargs)
        with patch('dock.copy_game',side_effect=stop_during_copy):
            self.assertTrue(self.product.tick([]))
        self.assertEqual((Path(self.dock.config['library'])/'ISO/Game.iso').read_bytes(),b'synthetic game content')
        self.assertEqual(self.product.flow.snapshot()['phase'],'returning_to_controller')
        roles=list(self.transport.roles)
        self.assertFalse(self.product.tick([]))
        self.assertEqual(self.transport.roles,roles)

    def test_usb_session_change_before_copy_never_uses_wrong_card(self):
        self.ready('none')
        game=next(g for g in self.dock.games() if g['availability']=='missing')
        self.dock.request_game_copy_from_hid(game['id'])
        self.transport.session_id='22'*16
        self.tick()
        self.assertEqual(self.product.snapshot()['phase'],'attention')
        self.assertFalse((Path(self.dock.config['library'])/'ISO/Game.iso').exists())

    def test_compatible_sram_import_and_return_preserve_both_private_state_formats(self):
        psp_sram, psp_state = self.retro_fixture()
        self.ready('all')
        rom = self.dock.retro_catalog()['roms'][0]
        pc_sram, pc_state = private_save_paths(self.dock.config['storage_root'],self.transport.serial,rom['id'])
        self.assertEqual(pc_sram.read_bytes(),b'a'*32768)
        pc_state.parent.mkdir(parents=True,exist_ok=True)
        pc_state.write_bytes(b'PC core-specific savestate, never compatible by extension')
        pc_sram.write_bytes(b'b'*32768)
        self.assertEqual(self.exit()['phase'],'safe_to_disconnect')
        self.assertEqual(psp_sram.read_bytes(),b'b'*32768)
        self.assertEqual(psp_state.read_bytes(),b'PSP savestate, private emulator format')
        self.assertEqual(pc_state.read_bytes(),b'PC core-specific savestate, never compatible by extension')
        self.assertFalse(list(self.psp.rglob('*.state')))
        self.assertTrue(self.product.snapshot()['unsupported_state_formats'])

    def test_retro_return_conflict_never_imports_psp_and_can_be_skipped(self):
        psp_sram, _ = self.retro_fixture()
        self.ready('all')
        rom = self.dock.retro_catalog()['roms'][0]
        pc_sram, _ = private_save_paths(self.dock.config['storage_root'],self.transport.serial,rom['id'])
        psp_sram.write_bytes(b'x'*32768)
        pc_sram.write_bytes(b'y'*32768)
        stopped = self.exit()
        self.assertEqual(stopped['phase'],'attention')
        row = self.dock.snapshot['retro_saves']['rows'][0]
        self.assertEqual(row['status'],'conflict')
        with self.assertRaisesRegex(ValueError,'dirección'):
            self.dock.sync_retro_save(rom['id'],'psp',row['revision'])
        self.dock.sync_retro_save(rom['id'],'skip',row['revision'])
        self.assertEqual(psp_sram.read_bytes(),b'x'*32768)
        self.assertEqual(pc_sram.read_bytes(),b'y'*32768)
        self.product.retry(stopped['offer_id'])
        for _ in range(4):self.tick()
        self.assertEqual(self.product.snapshot()['phase'],'safe_to_disconnect')
        self.assertEqual(pc_sram.read_bytes(),b'y'*32768)

    def test_worker_inventories_and_changes_roles_without_legacy_return_worker(self):
        with patch.object(self.dock,'complete_authorized_return',side_effect=AssertionError('legacy return during native session')), \
             patch.object(self.dock,'complete_queued_game_copy',side_effect=AssertionError('legacy copy during native session')):
            self.watch_once()
            self.assertEqual(self.dock.snapshot['product_flow']['phase'],'choosing')
            self.product.choose('none',self.product.snapshot()['offer_id'])
            self.watch_once()
            self.watch_once()
        self.assertEqual(self.dock.snapshot['product_flow']['phase'],'ready')
        self.assertEqual(self.transport.roles,[('controller','initial')])

    def test_native_ppsspp_launch_uses_pc_saves_only_after_real_controller_confirmation(self):
        self.ready('games')
        game = next(g for g in self.dock.games() if g['availability']=='ready')
        exe = self.home/'PPSSPPWindows64.exe'
        exe.write_bytes(b'synthetic executable, never execute')
        self.dock.config['emulator'] = str(exe)
        controls=Path(self.dock.config['memstick'])/'PSP/SYSTEM/controls.ini'
        controls.parent.mkdir(parents=True,exist_ok=True)
        controls.write_text('[ControlMapping]\nCircle=10-188\nCross=10-189\nSquare=10-190\nTriangle=10-191\nPause=1-111\n')
        process = Mock()
        process.poll.return_value = None
        with self.mock_windows(), \
             patch.object(self.dock,'prepared_runtime_path',return_value=exe), \
             patch.object(self.dock,'apply_game_graphics'), \
             patch('ppsspp_hid_session.validate_hid_only',side_effect=AssertionError('native composite is not legacy HID-only')), \
             patch('ppsspp_windows_flow.focus_async'), \
             patch('dock.subprocess.Popen',return_value=process) as spawn:
            self.dock.launch(game['id'])
        self.assertEqual(spawn.call_count,1)
        self.assertEqual(self.transport.phase,2)
        self.assertTrue(self.dock.session['pending_return'])
        self.assertNotIn('prepared_volume',self.dock.session)
        process.poll.return_value = 0
        with patch('ppsspp_windows_flow.return_to_xmb',return_value=False):
            self.assertFalse(self.dock.running())
        self.assertEqual(self.product.snapshot()['phase'],'ready')
        self.assertEqual(self.transport.roles,[('controller','initial')])

    def test_native_retro_launch_requires_same_bound_controller_and_native_profile(self):
        self.retro_fixture()
        self.ready('games')
        rom = self.dock.retro_catalog()['roms'][0]
        exe = self.home/'RetroArch/retroarch.exe'
        exe.parent.mkdir()
        exe.write_bytes(b'synthetic executable, never execute')
        core = exe.parent/'cores'/SYSTEMS['gba'][1]
        core.parent.mkdir()
        core.write_bytes(b'synthetic core, never execute')
        self.dock.config['retroarch'] = str(exe)
        process = Mock()
        process.poll.return_value = None
        with self.mock_windows(), \
             patch('dock.prepare_joypad_profile',wraps=app.prepare_joypad_profile) as profile, \
             patch('ppsspp_hid_session.validate_hid_only',side_effect=AssertionError('no legacy transport assertion')), \
             patch('ppsspp_windows_flow.focus_async'), \
             patch('dock.subprocess.Popen',return_value=process) as spawn:
            self.dock.launch_retro(rom['id'])
        self.assertEqual(spawn.call_count,1)
        self.assertEqual(profile.call_args.kwargs['product_id'],0x01CD)
        self.assertTrue(self.product.snapshot()['game_running'])
        self.assertTrue(self.dock.session['pending_return'])

    def test_stale_native_ready_snapshot_cannot_launch_after_role_changes(self):
        self.ready('games')
        game = next(g for g in self.dock.games() if g['availability']=='ready')
        self.transport.phase = 3
        with patch('dock.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'controller identity/phase'):
                self.dock.launch(game['id'])
        spawn.assert_not_called()
        self.assertFalse(self.dock.session.get('pending_return'))

    def test_live_disconnect_after_play_recovers_return_without_recapturing_baseline(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'old')
        pc_file=self.slot(self.pc_saves,'SAVE',b'old')
        self.ready('none')
        baseline=dict(self.product.entry['pc'])
        self.product.flow.set_game_running(True)
        self.product.flow.set_game_running(False)
        pc_file.write_bytes(b'new session progress')
        self.transport.connected=False
        self.tick()
        self.transport.connected=True
        self.transport.phase=1
        self.transport.session_id='22' * 16
        self.transport.host_confirmed=False
        self.tick()
        self.assertEqual(self.product.snapshot()['purpose'],'exit')
        self.assertEqual(self.product.entry['pc'],baseline)
        self.assertTrue(self.product.snapshot()['recovery_required'])
        self.assertTrue(self.product.snapshot()['can_retry'])
        self.assertEqual(self.transport.begins, 1)
        self.assertEqual(psp_file.read_bytes(), b'old')
        self.product.retry(self.product.snapshot()['offer_id'])
        begin = self.transport.begin_session
        def recovery_begin(context):
            journal = app.read_json(self.product.flow.path, {})
            self.assertEqual(journal['operation']['name'], 'request_role')
            self.assertEqual(journal['operation']['status'], 'started')
            self.assertEqual(journal['purpose'], 'exit')
            self.assertEqual(self.product.entry['pc'], baseline)
            return begin(context)
        with patch.object(self.transport, 'begin_session', side_effect=recovery_begin):
            for _ in range(4):self.tick()
        self.assertEqual(self.product.snapshot()['phase'],'safe_to_disconnect')
        self.assertEqual(self.transport.begins, 2)
        self.assertEqual(psp_file.read_bytes(),b'new session progress')
        self.assertEqual(self.product.entry['pc'],baseline)

    def test_orphan_emulator_recovery_blocks_retry_and_all_file_callbacks(self):
        psp_file=self.slot(self.psp/'PSP/SAVEDATA','SAVE',b'old')
        pc_file=self.slot(self.pc_saves,'SAVE',b'old')
        self.ready('none')
        self.product.flow.set_game_running(True)
        self.dock.session.update(running=True,pending_return=True,launcher='retroarch')
        app.atomic_json(self.dock.data/'session.json',self.dock.session)
        pc_file.write_bytes(b'possibly still changing')
        restarted=app.Dock(self.dock.data)
        self.transport.phase=1
        recovered=DockProductSession(restarted,self.transport)
        restarted.product_session,restarted.product_flow,restarted.native_transport=recovered,recovered.flow,self.transport
        recovered.tick(restarted.devices())
        self.assertTrue(restarted.recovery_required)
        self.assertFalse(recovered.snapshot()['can_retry'])
        with self.assertRaisesRegex(ValueError,'recuperación'):
            recovered.retry(recovered.snapshot()['offer_id'])
        context={'identity':{'session_id':self.transport.session_id,'volume_serial':self.transport.serial},'direction':'to_psp'}
        with self.assertRaisesRegex(ValueError,'recuperación'):
            recovered.sync_return(context)
        self.assertEqual(psp_file.read_bytes(),b'old')


if __name__=='__main__':
    unittest.main()
