"""PC-only media integration: no PSP reads, emulator launches or sound output."""
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import dock as app
from psp_dock.media.psp_xmb_media import XMBAssetCache, media_key


class MediaIntegrationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(app.os.environ, {'PSP_DOCK_TEST_NO_REAL_USB': '1'})
        env.start(); self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dock = app.Dock(Path(temp.name) / 'pc')
        self.addCleanup(self.close_media)
        self.game = {'id':'a'*24, 'relative':'ISO/Game.iso', 'format':'ISO',
                     'title':'Synthetic', 'size':100, 'modified_ns':123}
        self.dock.last_games = [self.game]
        self.dock.catalog_volume = 'fixture-card'
        self.cache = XMBAssetCache(Path(self.dock.config['storage_root']) / 'xmb-assets')
        self.cache.root.mkdir(parents=True)
        self.key = media_key(volume='fixture-card', game=self.game)
        stream = io.BytesIO()
        Image.new('RGB', (48, 27), (20, 50, 70)).save(stream, 'PNG')
        self.png = stream.getvalue()

    def close_media(self):
        if self.dock._background_cache is not None:
            self.dock._background_cache.close(wait=True)

    def test_hd_reads_cached_original_only_and_reuses_single_worker(self):
        original = self.cache.path(self.key, 'background')
        original.write_bytes(self.png)
        with patch.object(self.dock, 'root', side_effect=AssertionError('No USB root')), \
             patch.object(self.dock, 'devices', side_effect=AssertionError('No USB discovery')), \
             patch('psp_dock.media.psp_xmb_media.read_resource', side_effect=AssertionError('No game re-extraction')), \
             patch('psp_dock.media.xmb_backgrounds.TARGET_WIDTH', 96):
            self.assertIsNone(self.dock.xmb_background_hd(self.game['id']))
            worker = self.dock._background_cache
            self.assertTrue(worker.wait(timeout=10))
            ready = self.dock.xmb_background_hd(self.game['id'])
            self.assertIs(self.dock._background_cache, worker)
            with Image.open(io.BytesIO(ready)) as image:
                self.assertEqual(image.size, (96, 54))
        self.assertEqual(original.read_bytes(), self.png)

    def test_missing_original_different_card_or_shutdown_never_starts_worker(self):
        self.assertIsNone(self.dock.xmb_background_hd(self.game['id']))
        self.cache.path(self.key, 'background').write_bytes(self.png)
        self.dock.catalog_volume = 'another-card'
        self.assertIsNone(self.dock.xmb_background_hd(self.game['id']))
        self.dock.catalog_volume = 'fixture-card'
        self.dock.stop.set()
        self.assertIsNone(self.dock.xmb_background_hd(self.game['id']))
        self.assertIsNone(self.dock._background_cache)

    def test_optional_missing_image_library_leaves_original(self):
        self.cache.path(self.key, 'background').write_bytes(self.png)
        with patch.dict('sys.modules', {'psp_dock.media.xmb_backgrounds': None}):
            self.assertIsNone(self.dock.xmb_background_hd(self.game['id']))
        self.assertEqual(self.cache.cached(self.key, 'background'), self.png)

    def test_ui_click_never_queues_behind_copy_or_plays_over_game(self):
        player = self.dock.ui_sound_player = Mock()
        player.play.return_value = True
        self.assertTrue(self.dock.play_ui_sound('cursor', time.time()*1000)['played'])
        self.dock.process = Mock()
        self.dock.process.poll.return_value = None
        self.assertFalse(self.dock.play_ui_sound('cursor', time.time()*1000)['played'])
        self.dock.process = None
        self.dock.transfer_status['active'] = True
        self.assertFalse(self.dock.play_ui_sound('cursor', time.time()*1000)['played'])
        self.dock.transfer_status['active'] = False
        with patch.object(self.dock, 'lock', Mock()) as locked:
            locked.acquire.return_value = False
            self.assertFalse(self.dock.play_ui_sound('cursor', time.time()*1000)['played'])
            locked.acquire.assert_called_once_with(blocking=False)
        self.assertEqual(player.play.call_count, 1)


if __name__ == '__main__': unittest.main()
