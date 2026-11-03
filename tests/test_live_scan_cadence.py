"""Ensure image requests don't rescan real PSP storage per thumbnail."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from dock import Dock
from tests.test_psp_artwork import PNG, write_fixture_iso


class LiveScanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.psp = self.home / 'fake-psp'
        (self.psp / 'PSP/SAVEDATA').mkdir(parents=True)
        self.game = self.psp / 'ISO/Example.iso'
        write_fixture_iso(self.game)
        self.dock = Dock(self.home / 'data')
        self.dock.settings({'psp_root': str(self.psp)})

    def tearDown(self):
        self.tmp.cleanup()

    def test_artwork_uses_audited_catalog_instead_of_repeated_usb_scan(self):
        game_id = self.dock.games()[0]['id']
        with patch.object(self.dock, 'games', side_effect=AssertionError('repeated FAT inventory')):
            self.assertEqual(self.dock.icon(game_id), PNG)
            self.assertEqual(self.dock.icon(game_id), PNG)
        self.assertEqual(len(list((self.dock.data / 'artwork').glob('*.png'))), 1)

    def test_artwork_rejects_stale_catalog_if_memory_stick_identity_changes(self):
        game_id = self.dock.games()[0]['id']
        with patch('dock.volume_identity', return_value='Different-FAT-serial'), \
             patch.object(self.dock, 'games', side_effect=AssertionError('no rescans')):
            self.assertIsNone(self.dock.icon(game_id))
        self.assertFalse((self.dock.data / 'artwork').exists())

    def test_connected_watch_reuses_unchanged_full_scan(self):
        count = 0
        def finish_wait(_delay):
            nonlocal count
            count += 1
            if count >= 3:
                self.dock.stop.set()

        original = self.dock.state
        with patch.object(self.dock.stop,'wait',side_effect=finish_wait), \
             patch.object(self.dock, 'state', wraps=original) as scanned:
            self.dock.watch()
        self.assertEqual(count,3)
        self.assertEqual(scanned.call_count,1,
                         'unchanged USBSTOR must not rehash SAVEDATA every 3 seconds')
        self.assertEqual(self.dock.snapshot['library_source'], 'psp_live')
        self.assertFalse(self.dock.snapshot['scanning'])

    def test_disconnection_invalidates_held_storage_snapshot(self):
        self.dock.state()
        def one_pass(_seconds):
            self.dock.stop.set()
        with patch.object(self.dock.stop, 'wait', side_effect=one_pass):
            self.dock.watch()
        self.dock.stop.clear()
        with patch.object(self.dock, 'root', return_value=None), \
             patch.object(self.dock.stop, 'wait', side_effect=one_pass):
            self.dock.watch()
        self.assertEqual(self.dock.snapshot['library_source'], 'cached_manifest')
        self.assertIsNone(self.dock.snapshot['selected_device'])

    def test_waiting_without_psp_polls_usb_with_empty_and_forbidden_catalogs(self):
        self.assertIsNone(self.dock.cached_game_results)
        self.assertIsNone(self.dock.cached_retro_results)
        publications = []

        def finish_wait(_delay):
            publications.append(dict(self.dock.snapshot))
            if len(publications) == 3:
                self.dock.stop.set()

        unavailable = {'connected':False, 'can_play':False, 'mode':'await_hid'}
        with patch.object(self.dock, 'devices', return_value=[]) as devices, \
             patch.object(self.dock.native_transport, 'observe', return_value=None) as observe, \
             patch.object(self.dock, 'controller_status', return_value=unavailable), \
             patch.object(self.dock, 'state', side_effect=AssertionError('waiting must not rebuild empty catalogs')) as state, \
             patch.object(self.dock, 'games', side_effect=AssertionError('forbidden PC game walk')) as games, \
             patch.object(self.dock, 'retro_catalog', side_effect=AssertionError('forbidden PC ROM walk')) as retro, \
             patch.object(self.dock.stop, 'wait', side_effect=finish_wait):
            self.dock.watch()

        self.assertGreaterEqual(devices.call_count, 3)
        self.assertEqual(observe.call_count, 3)
        self.assertTrue(all(call.args == ([],) for call in observe.call_args_list))
        state.assert_not_called()
        games.assert_not_called()
        retro.assert_not_called()
        self.assertEqual(len(publications), 3)
        for view in publications:
            self.assertEqual(view['connection_state'], 'waiting_psp')
            self.assertFalse(view['scanning'])
            self.assertIsNone(view['error'])
            self.assertIsNone(view['selected_device'])
            self.assertFalse(view['can_launch_game'])
            self.assertFalse(view['can_launch_retro'])
        stages = self.dock.product_session.diagnostics.snapshot()['stages']
        self.assertEqual(stages['ui.publish_legacy_waiting']['count'], 3)
        self.assertNotIn('ui.publish_legacy', stages)

    def test_waiting_preserves_recovery_copy_queue_and_active_game(self):
        self.dock.session.update(running=True, pending_return=True,
                                 prepared_volume='previous-card', exit_requested=True)
        self.dock.recovery_required = True
        self.dock.process = Mock()
        self.dock.process.poll.return_value = None
        self.dock.copy_queue = {'status':'waiting_for_storage', 'game_id':'a'*24,
                                'volume_serial':'previous-card'}
        self.dock.snapshot.update(can_launch_game=True, can_launch_retro=True,
                                  preflight={'connected':True, 'saves_prepared':True})
        session_before = dict(self.dock.session)
        queue_before = dict(self.dock.copy_queue)
        journals = {
            self.dock.data/'session.json': json.dumps(session_before),
            self.dock.copy_queue_path: json.dumps(queue_before),
            self.dock.data/'baseline-previous-card.json': '{"SAVE":"keep-baseline"}',
        }
        for path, contents in journals.items():
            path.write_text(contents, encoding='utf-8')

        with patch.object(self.dock, 'devices', return_value=[]), \
             patch.object(self.dock.native_transport, 'observe', return_value=None), \
             patch.object(self.dock, 'controller_status', return_value={'connected':False}), \
             patch.object(self.dock, 'state', side_effect=AssertionError('no catalog scan')) as state, \
             patch.object(self.dock, 'complete_queued_game_copy', side_effect=AssertionError('no queued copy')) as copy, \
             patch.object(self.dock, 'complete_authorized_return', side_effect=AssertionError('no return write')) as returning, \
             patch.object(self.dock.stop, 'wait', side_effect=lambda delay:self.dock.stop.set()):
            self.dock.watch()

        state.assert_not_called()
        copy.assert_not_called()
        returning.assert_not_called()
        self.assertEqual(self.dock.session, session_before)
        self.assertEqual(self.dock.copy_queue, queue_before)
        self.assertEqual(self.dock.snapshot['session'], session_before)
        self.assertEqual(self.dock.snapshot['copy_queue'], queue_before)
        self.assertTrue(self.dock.snapshot['recovery_required'])
        self.assertIsNotNone(self.dock.process)
        self.assertFalse(self.dock.snapshot['preflight']['connected'])
        self.assertFalse(self.dock.snapshot['can_launch_game'])
        self.assertFalse(self.dock.snapshot['can_launch_retro'])
        for path, contents in journals.items():
            self.assertEqual(path.read_text(encoding='utf-8'), contents)

    def test_reconnection_audits_changed_catalog_after_waiting_without_scans(self):
        unavailable = {'connected':False, 'can_play':False, 'mode':'await_hid'}
        with patch.object(self.dock.native_transport, 'observe', return_value=None), \
             patch.object(self.dock.stop, 'wait', side_effect=lambda delay:self.dock.stop.set()):
            self.dock.watch()
        self.assertEqual(self.dock.snapshot['library_source'], 'psp_live')
        self.assertTrue(self.dock.last_storage_scan_identity)
        self.dock.stop.clear()
        attached = False
        publications = []
        original_state = self.dock.state

        def devices():
            return [{'root':str(self.psp)}] if attached else []

        def state_after_attachment(*args, **kwargs):
            self.assertTrue(attached, 'no full publication while the PSP is absent')
            return original_state(*args, **kwargs)

        def finish_wait(_delay):
            nonlocal attached
            publications.append(dict(self.dock.snapshot))
            if len(publications) == 2:
                self.assertEqual(self.dock.last_storage_scan_identity, '')
                write_fixture_iso(self.psp/'ISO/New-attachment.iso')
                attached = True
            elif len(publications) == 4:
                self.dock.stop.set()

        with patch.object(self.dock, 'devices', side_effect=devices), \
             patch.object(self.dock.native_transport, 'observe', return_value=None) as observe, \
             patch.object(self.dock, 'controller_status', return_value=unavailable), \
             patch.object(self.dock, 'state', side_effect=state_after_attachment) as scanned, \
             patch.object(self.dock.stop, 'wait', side_effect=finish_wait):
            self.dock.watch()

        self.assertEqual(observe.call_count, 4)
        self.assertEqual(scanned.call_count, 1)
        for view in publications[:2]:
            self.assertIsNone(view['selected_device'])
            self.assertEqual(view['library_source'], 'cached_manifest')
            self.assertFalse(view['can_launch_game'])
        self.assertEqual(self.dock.snapshot['selected_device'], str(self.psp))
        self.assertIsNone(self.dock.snapshot['error'])
        self.assertTrue(any(game['relative'] == 'ISO/New-attachment.iso'
                            for game in self.dock.snapshot['games']))
        stages = self.dock.product_session.diagnostics.snapshot()['stages']
        self.assertEqual(stages['ui.publish_legacy_waiting']['count'], 2)
        self.assertEqual(stages['ui.publish_legacy']['count'], 2)

    def test_verified_legacy_controller_keeps_normal_catalog_publication(self):
        controller = {'connected':True, 'can_play':True, 'mode':'hid_manual',
                      'synthetic_storage':False}
        view = {**self.dock.snapshot, 'controller_status':controller,
                'connection_state':'dock_connected', 'can_launch_game':False}
        with patch.object(self.dock, 'devices', return_value=[]), \
             patch.object(self.dock.native_transport, 'observe', return_value=None), \
             patch.object(self.dock, 'controller_status', return_value=controller), \
             patch.object(self.dock, 'state', return_value=view) as scanned, \
             patch.object(self.dock.stop, 'wait', side_effect=lambda delay:self.dock.stop.set()):
            self.dock.watch()
        scanned.assert_called_once_with()
        self.assertEqual(self.dock.snapshot['connection_state'], 'dock_connected')
        self.assertTrue(self.dock.snapshot['controller_status']['connected'])
        self.assertNotIn('ui.publish_legacy_waiting',
                         self.dock.product_session.diagnostics.snapshot()['stages'])

    def test_native_app_in_xmb_usb_does_not_start_legacy_inventory(self):
        native = self.psp/'PSP/GAME/PSPDockNative/EBOOT.PBP'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'installed application marker, not an identity proof')
        with patch.object(self.dock.stop, 'wait', side_effect=lambda delay:self.dock.stop.set()) as wait, \
             patch.object(self.dock, 'state', side_effect=AssertionError('expensive legacy inventory')), \
             patch.object(self.dock, 'save_plan', side_effect=AssertionError('premature save scan')), \
             patch.object(self.dock, 'start_xmb_media_prefetch', side_effect=AssertionError('premature media scan')):
            self.dock.watch()
        self.assertEqual(self.dock.snapshot['controller_status']['mode'], 'await_native')
        self.assertFalse(self.dock.product_flow.enabled)
        self.assertFalse(self.dock.snapshot['scanning'])
        self.assertFalse(self.dock.snapshot['can_launch_game'])
        wait.assert_called_once_with(0.25)

    def test_native_marker_does_not_override_explicit_legacy_return(self):
        native = self.psp/'PSP/GAME/PSPDockNative/EBOOT.PBP'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'marker')
        devices = [{'root':str(self.psp)}]
        self.assertTrue(self.dock.awaiting_native_app(devices))
        self.dock.session['pending_return'] = True
        self.assertFalse(self.dock.awaiting_native_app(devices))


if __name__ == '__main__':
    unittest.main()
