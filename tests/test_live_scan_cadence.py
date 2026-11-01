"""Ensure image requests don't rescan real PSP storage per thumbnail."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
