"""Cancellation and progress always concern copies PSP→PC, never PSP writes."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from dock import Dock, TransferCancelled, copy_game


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.psp = self.home/'fake-psp'
        (self.psp/'PSP/SAVEDATA').mkdir(parents=True)
        (self.psp/'ISO').mkdir()
        self.dock = Dock(self.home/'dock')
        self.dock.settings({'psp_root': str(self.psp)})

    def tearDown(self):
        self.temp.cleanup()

    def make_game(self, name, mb=2):
        source = self.psp/'ISO'/(name+'.iso')
        source.write_bytes((name.encode() * (1024*1024//len(name)+1))[:1024*1024]*mb)
        return source

    def test_chunk_progress_and_cancellation_leave_no_partial_iso(self):
        source = self.make_game('Example',3)
        target = self.home/'pc/Example.iso'
        cancel = threading.Event()
        chunks = []
        def chunk(n):
            chunks.append(n)
            cancel.set()
        with self.assertRaisesRegex(TransferCancelled,'cancelada'):
            copy_game(source, target, on_chunk=chunk, cancel=cancel)
        self.assertEqual(chunks, [1024*1024])
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob('.dock-transfer-*')))
        self.assertEqual(source.stat().st_size, 3*1024*1024)

    def test_one_shot_cancel_works_while_transfer_owns_dock_lock(self):
        source = self.make_game('Game',4)
        identifier = self.dock.games()[0]['id']
        reading = threading.Event()
        proceed = threading.Event()
        original = self.dock._update_transfer_delta
        error = []
        def slow_progress(n):
            original(n)
            reading.set()
            proceed.wait(10)
        def transfer():
            try:
                with self.dock.lock:
                    self.dock.transfer([identifier])
            except Exception as exc:
                error.append(exc)
        with patch.object(self.dock,'_update_transfer_delta',side_effect=slow_progress):
            thread = threading.Thread(target=transfer)
            thread.start()
            try:
                self.assertTrue(reading.wait(10), 'The first chunk did not copy')
                progress = self.dock.transfer_progress()
                self.assertTrue(progress['active'])
                self.assertEqual(progress['phase'], 'copying')
                self.assertEqual(progress['total_bytes'],source.stat().st_size)
                self.assertGreater(progress['copied_bytes'],0)
                self.assertTrue(self.dock.cancel_transfer()['cancel_requested'])
            finally:
                proceed.set()
                thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(error),1)
        self.assertIsInstance(error[0],TransferCancelled)
        final = self.dock.transfer_progress()
        self.assertEqual(final['phase'],'cancelled')
        self.assertFalse(final['active'])
        self.assertFalse((Path(self.dock.config['library'])/'ISO/Game.iso').exists())
        self.assertFalse(list((Path(self.dock.config['library'])/'ISO').glob('.dock-transfer-*')))
        with self.assertRaisesRegex(ValueError,'No hay'):
            self.dock.cancel_transfer()

    def test_previous_verified_games_survive_cancel_of_second_game(self):
        first = self.make_game('First', 1)
        second = self.make_game('Second', 3)
        games = self.dock.games()
        ids = [game['id'] for game in games]
        original = self.dock._update_transfer_delta
        def cancel_during_second(n):
            original(n)
            if self.dock.transfer_progress()['game_index'] == 2:
                self.dock.cancel_transfer()
        with patch.object(self.dock,'_update_transfer_delta',side_effect=cancel_during_second):
            with self.assertRaises(TransferCancelled):
                self.dock.transfer(ids)
        library = Path(self.dock.config['library'])/'ISO'
        self.assertEqual((library/'First.iso').read_bytes(),first.read_bytes())
        self.assertFalse((library/'Second.iso').exists())
        self.assertEqual(self.dock.transfer_progress()['phase'],'cancelled')
        self.dock.settings({'psp_root': ''})
        with patch('dock.windows_devices', return_value=[]):
            offline=Dock(self.home/'dock')
            self.assertEqual(next(g for g in offline.games()
                                  if g['title']=='First')['availability'],'ready')
        self.dock.settings({'psp_root': str(self.psp)})
        self.dock.transfer([ids[1]])
        self.assertEqual(self.dock.transfer_progress()['phase'],'done')
        self.assertEqual(self.dock.transfer_progress()['copied_bytes'],second.stat().st_size)
        self.assertEqual((library/'Second.iso').read_bytes(),second.read_bytes())
        self.assertTrue(all(g['availability']=='ready' for g in self.dock.games()))

    def test_eboot_and_assets_are_cancelled_without_a_partial_directory(self):
        source = self.psp/'PSP/GAME/PocketApp'
        source.mkdir(parents=True)
        (source/'EBOOT.PBP').write_bytes(b'PBP'+b'x'*(1024*1024))
        (source/'assets').mkdir()
        (source/'assets/image.bin').write_bytes(b'y'*1048576)
        target = self.home/'pc/PSP/GAME/PocketApp'
        cancel=threading.Event()
        def chunk(n):
            cancel.set()
        with self.assertRaises(TransferCancelled):
            copy_game(source,target,on_chunk=chunk,cancel=cancel)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob('.dock-transfer-*')))
        cancel.clear()
        result=copy_game(source,target,cancel=cancel)
        self.assertEqual(len(result),64)
        self.assertEqual((target/'assets/image.bin').read_bytes(),b'y'*1048576)

    def test_path_symlinks_are_rejected_without_a_partial_copy(self):
        source = self.psp/'PSP/GAME/Symlink'
        source.mkdir(parents=True)
        (source/'EBOOT.PBP').write_bytes(b'fixture')
        outside=self.home/'somewhere'
        outside.write_bytes(b'data')
        try:
            (source/'alias.bin').symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('Windows account cannot create symlinks')
        target = self.home/'pc/PSP/GAME/Symlink'
        with self.assertRaises(ValueError):
            copy_game(source,target)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
