"""PC-only image and catalog tests. No physical PSP or PPSSPP processes."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

import dock as backend
from dock import Dock
from tests.test_psp_artwork import PNG, fixture_png, write_fixture_iso


class ArtworkCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.psp1 = self.home / 'psp-a'
        self.psp2 = self.home / 'psp-b'
        for root in (self.psp1, self.psp2):
            (root / 'PSP/SAVEDATA').mkdir(parents=True)
        self.first = self.psp1 / 'ISO/Same Title.iso'
        self.second = self.psp2 / 'ISO/Same Title.iso'
        write_fixture_iso(self.first, PNG)
        write_fixture_iso(self.second, fixture_png((220, 80, 45)))
        self.dock = Dock(self.home / 'data')
        self.dock.settings({'psp_root': str(self.psp1)})

    def tearDown(self):
        self.temp.cleanup()

    def test_same_relative_game_id_from_two_psps_never_shares_artwork(self):
        original_stat = self.first.stat()
        os.utime(self.second, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        def pretend_distinct_volumes(root):
            return '100001' if Path(root) == self.psp1 else '200002'

        # Both synthetic folders are on the same PC disk; substitute two
        # real-looking *different PSP volume serials* so this tests serial
        # isolation rather than incidental timestamp differences.
        with patch('dock.volume_identity', side_effect=pretend_distinct_volumes):
            first_game = self.dock.games()[0]
            first_png = self.dock.icon(first_game['id'])
            self.assertEqual(first_png, PNG)
            self.dock.settings({'psp_root': str(self.psp2)})
            second_game = self.dock.games()[0]
            self.assertEqual(first_game['id'], second_game['id'])
            second_png = self.dock.icon(second_game['id'])
            self.assertNotEqual(first_png, second_png)
            self.assertEqual(len(list((self.home / 'data/artwork').glob('*.png'))), 2)
            self.dock.settings({'psp_root': str(self.psp1)})
            self.assertEqual(self.dock.icon(first_game['id']), first_png)

    def test_no_psp_reads_when_offline_if_artwork_was_cached(self):
        game_id = self.dock.games()[0]['id']
        self.assertEqual(self.dock.icon(game_id), PNG)
        original = self.first.read_bytes()
        self.dock.settings({'psp_root': ''})
        with patch('dock.windows_devices', return_value=[]):
            offline = Dock(self.home / 'data')
            with patch('dock.read_icon', side_effect=AssertionError('Read from offline PSP')):
                self.assertEqual(offline.icon(game_id), PNG)
            self.assertEqual(offline.state()['library_source'], 'cached_manifest')
        self.assertEqual(self.first.read_bytes(), original)

    def test_verified_pc_copy_survives_restart_and_stays_distinct_from_psp_presence(self):
        game_id = self.dock.games()[0]['id']
        self.dock.transfer([game_id])
        self.assertEqual(self.dock.games()[0]['availability'], 'ready')
        self.dock.settings({'psp_root': ''})
        with patch('dock.windows_devices', return_value=[]):
            offline = Dock(self.home / 'data')
            state = offline.state()
            self.assertEqual(state['games'][0]['availability'], 'ready')
            self.assertEqual(state['library_source'], 'cached_manifest')
            self.assertFalse(state['preflight']['connected'])
            self.assertFalse(state['preflight']['can_start_hid'])
            # A corrupted PC copy may not be treated as verified/ready.
            local = Path(offline.config['library']) / 'ISO/Same Title.iso'
            value = bytearray(local.read_bytes())
            value[-1] ^= 3
            local.write_bytes(value)
            self.assertEqual(offline.games()[0]['availability'], 'different')

    def test_failed_inventory_cannot_replace_previous_complete_manifest(self):
        self.dock.games()
        manifest = self.home / 'data/inventory-cache.json'
        initial = manifest.read_bytes()
        original = backend.game_inventory

        def interrupted(root):
            if Path(root) == self.psp1:
                raise OSError('Simulated unplug during read-only scan')
            return original(root)

        with patch('dock.game_inventory', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'unplug'):
                self.dock.games()
        self.assertEqual(manifest.read_bytes(), initial)
        self.assertEqual(self.dock.games()[0]['title'], 'Same Title')

    def test_corrupt_cached_png_is_rejected_not_served(self):
        game_id = self.dock.games()[0]['id']
        self.dock.icon(game_id)
        cached = next((self.home / 'data/artwork').glob('*.png'))
        cached.write_bytes(b'\x89PNG\r\n\x1a\nFAKE')
        with self.assertRaisesRegex(ValueError, 'Icono cacheado'):
            self.dock.icon(game_id)


if __name__ == '__main__':
    unittest.main()
