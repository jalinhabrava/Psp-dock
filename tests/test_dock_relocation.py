"""PC data relocation preserves PSP saves and only changes local settings."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock


class OneRootMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.psp=self.root/'synthetic-psp'
        (self.psp/'PSP/SAVEDATA').mkdir(parents=True)
        (self.psp/'PSP/SAVEDATA/ORIGINAL.BIN').write_bytes(b'ONLY_ON_PSP')
        self.dock=Dock(self.root/'config')
        self.dock.settings({'psp_root':str(self.psp)})
        self.local_game=Path(self.dock.config['library'])/'ISO/game.iso'
        self.local_game.parent.mkdir(parents=True)
        self.local_game.write_bytes(b'LOCAL_GAME')
        self.save=Path(self.dock.config['memstick'])/'PSP/SAVEDATA/PLAYER/SAVE.BIN'
        self.save.parent.mkdir(parents=True)
        self.save.write_bytes(b'LOCAL_SAVE')
        self.texture=Path(self.dock.config['memstick'])/'PSP/TEXTURES/ULJM05558/textures.zip'
        self.texture.parent.mkdir(parents=True)
        self.texture.write_bytes(b'SYNTHETIC_HD')
        self.art=self.dock.data/'xmb-assets/preview.mp3'
        self.art.parent.mkdir(parents=True)
        self.art.write_bytes(b'SYNTHETIC_PREVIEW')

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_new_path_preserves_originals_and_updates_only_pc_config(self):
        destination=self.root/'new-library'
        report=self.dock.relocate_storage(str(destination))
        self.assertTrue(report['old_files_preserved'])
        self.assertEqual(self.dock.config['storage_root'],str(destination))
        self.assertEqual(self.dock.config['library'],str(destination/'library'))
        self.assertEqual(self.dock.config['memstick'],str(destination/'memstick'))
        self.assertEqual((destination/'library/ISO/game.iso').read_bytes(),b'LOCAL_GAME')
        self.assertEqual((destination/'memstick/PSP/SAVEDATA/PLAYER/SAVE.BIN').read_bytes(),b'LOCAL_SAVE')
        self.assertEqual((destination/'memstick/PSP/TEXTURES/ULJM05558/textures.zip').read_bytes(),b'SYNTHETIC_HD')
        self.assertEqual((destination/'xmb-assets/preview.mp3').read_bytes(),b'SYNTHETIC_PREVIEW')
        self.assertEqual(self.local_game.read_bytes(),b'LOCAL_GAME')
        self.assertEqual(self.save.read_bytes(),b'LOCAL_SAVE')
        self.assertEqual((self.psp/'PSP/SAVEDATA/ORIGINAL.BIN').read_bytes(),b'ONLY_ON_PSP')
        self.assertEqual(json.loads((self.dock.data/'settings.json').read_text())['storage_root'],str(destination))
        self.assertIsNone(self.dock.session.get('prepared_volume'))

    def test_refuses_relocation_while_game_or_return_is_pending(self):
        self.dock.session['pending_return']=True
        target=self.root/'no-write'
        with self.assertRaises(ValueError):
            self.dock.relocate_storage(str(target))
        self.assertFalse(target.exists())
        self.dock.session['pending_return']=False
        self.dock.xmb_media_progress['active']=True
        with self.assertRaisesRegex(ValueError,'copias'):
            self.dock.relocate_storage(str(target))
        self.assertFalse(target.exists())

    def test_cannot_change_only_storage_root_via_regular_settings(self):
        new=self.root/'wrong'
        with self.assertRaisesRegex(ValueError,'migración'):
            self.dock.settings({'storage_root':str(new)})
        self.assertFalse(new.exists())


if __name__=='__main__':
    unittest.main()
