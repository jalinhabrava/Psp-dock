"""PC-only media integration: no PSP reads, emulator launches or sound output."""
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from PIL import Image
import dock as app
from psp_xmb_media import XMBAssetCache, media_key, valid_mp3_header


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
             patch('psp_xmb_media.read_resource', side_effect=AssertionError('No game re-extraction')), \
             patch('xmb_backgrounds.TARGET_WIDTH', 96):
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
        with patch.dict('sys.modules', {'xmb_backgrounds': None}):
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

    def test_preview_uses_only_pc_cache_and_stops_after_newer_request(self):
        import time
        player = self.dock.preview_player = Mock()
        player.supported = True
        player.play.return_value = True
        original = b'ID3' + b'\0' * 200
        self.cache.path(self.key, 'sound').write_bytes(original)
        stamp = time.time() * 1000
        with patch.object(self.dock, 'root', side_effect=AssertionError('No USB root')), \
             patch('psp_xmb_media.read_resource', side_effect=AssertionError('No new ISO read')):
            played = self.dock.play_game_preview(self.game['id'], stamp)
            self.assertTrue(played['native'])
            self.assertTrue(played['played'])
            player.play.assert_called_once_with(self.cache.path(self.key, 'sound'))
            stopped = self.dock.play_game_preview(None, stamp + 1)
            self.assertEqual(stopped['reason'], 'stopped')
            player.stop.assert_called_once()
            old = self.dock.play_game_preview(self.game['id'], stamp)
            self.assertEqual(old['reason'], 'superseded')
            self.assertEqual(player.play.call_count, 1)

    def test_preview_rejects_unknown_game_and_cannot_play_over_running_game(self):
        player = self.dock.preview_player = Mock()
        player.supported = True
        stamp = time.time() * 1000
        for name in ('../game', 'b' * 24, 4):
            if name == 'b' * 24:
                self.assertFalse(self.dock.play_game_preview(name, stamp)['played'])
            else:
                with self.assertRaises(ValueError):
                    self.dock.play_game_preview(name, stamp)
        with self.assertRaises(ValueError):
            self.dock.play_game_preview(None, float('nan'))
        self.assertFalse(self.dock.play_game_preview(None, 0)['played'])
        self.cache.path(self.key, 'sound').write_bytes(b'ID3' + b'\0' * 200)
        with patch.object(self.dock, 'running', return_value=True):
            self.assertFalse(self.dock.play_game_preview(self.game['id'], stamp + 2)['played'])
        player.play.assert_not_called()

    def test_prefetch_prioritizes_sounds_and_retries_interrupted_passes(self):
        root = Path(self.dock.data) / 'synthetic-mounted-psp'
        root.mkdir()
        self.dock.snapshot = {'selected_device': str(root), 'scanning': False}
        self.dock.last_games = [self.game]
        self.dock.xmb_media = Mock(return_value=None)
        self.dock.start_xmb_media_prefetch()
        self.dock.xmb_media_worker.join(timeout=5)
        self.assertFalse(self.dock.xmb_media_worker.is_alive())
        self.assertEqual([call.args[1] for call in self.dock.xmb_media.call_args_list],
                         ['sound', 'icon', 'background', 'overlay'])
        self.assertFalse(self.dock.xmb_media_progress['active'])

        self.dock.xmb_media_catalog_key = None
        self.dock.xmb_media.reset_mock()
        def interruption(*args):
            if args[1] == 'sound':
                self.dock.storage_polling_paused = True
        self.dock.xmb_media.side_effect = interruption
        self.dock.storage_polling_paused = False
        self.dock.start_xmb_media_prefetch()
        self.dock.xmb_media_worker.join(timeout=5)
        self.assertIsNone(self.dock.xmb_media_catalog_key)
        self.assertFalse(self.dock.xmb_media_progress['active'])
        self.dock.storage_polling_paused = False
        self.dock.xmb_media.side_effect = None
        self.dock.start_xmb_media_prefetch()
        self.dock.xmb_media_worker.join(timeout=5)
        self.assertIsNotNone(self.dock.xmb_media_catalog_key)

    def test_mp3_cache_accepts_bare_frame_and_id3_but_not_random_bytes(self):
        path = self.cache.path(self.key, 'sound')
        for data in (b'ID3'+b'\0'*200, b'\xff\xfb\x90\x64'+b'\0'*200):
            path.write_bytes(data)
            self.assertTrue(valid_mp3_header(data))
            self.assertEqual(self.cache.cached(self.key, 'sound'), data)
        for data in (b'\xff\x00\x00\x00', b'\xff\xfb\xff\xff', b'evil.exe'):
            path.write_bytes(data)
            self.assertFalse(valid_mp3_header(data))
            self.assertIsNone(self.cache.cached(self.key, 'sound'))

    def test_native_prefetch_requires_verified_storage_for_each_resource(self):
        root = self.dock.data/'native-psp-fixture'
        root.mkdir()
        self.dock.snapshot = {'selected_device':str(root), 'scanning':False}
        self.dock.product_flow.enabled = True
        verified = Mock()
        self.dock.native_transport = SimpleNamespace(
            current={'storage_ready':True, 'host_confirmed':True}, assert_storage=verified)
        observed = []
        def read(game, kind):
            observed.append(kind)
            self.dock.native_transport.current = {'storage_ready':False,'host_confirmed':True}
        self.dock.xmb_media = Mock(side_effect=read)
        self.dock.start_xmb_media_prefetch()
        self.dock.xmb_media_worker.join(timeout=5)
        self.assertEqual(observed,['sound'], 'Revoking Sony storage stops further reads')
        verified.assert_called_once_with(str(root))
        self.assertIsNone(self.dock.xmb_media_catalog_key)
        self.assertFalse(self.dock.xmb_media_progress['active'])


if __name__ == '__main__': unittest.main()
