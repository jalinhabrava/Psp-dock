from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storage_layout import (
    StorageLocationError, fixed_local_folder, migrate_pc_data,
    choose_pc_storage_parent,
)


class PCStorageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.old=self.root/'old-data'
        for name in ('library/ISO','memstick/PSP/SAVEDATA/SAVE1',
                     'memstick/PSP/TEXTURES/ULJM05558','artwork','xmb-assets'):
            (self.old/name).mkdir(parents=True,exist_ok=True)
        (self.old/'library/ISO/game.iso').write_bytes(b'GAME_FIXTURE')
        (self.old/'memstick/PSP/SAVEDATA/SAVE1/data.bin').write_bytes(b'ONLY_SAVE')
        (self.old/'memstick/PSP/TEXTURES/ULJM05558/textures.zip').write_bytes(b'FAKE_FIXTURE')
        (self.old/'xmb-assets/preview.png').write_bytes(b'PNG_FIXTURE')

    def tearDown(self):
        self.tmp.cleanup()

    def migrate(self, target):
        return migrate_pc_data(
            target=target,library=self.old/'library',
            memstick=self.old/'memstick',old_data=self.old,
        )

    def test_relocates_games_saves_hd_packs_and_media_without_deleting_old(self):
        target=self.root/'another-drive-folder'
        report=self.migrate(target)
        self.assertEqual(report['files'],4)
        self.assertTrue(report['old_files_preserved'])
        for rel in ('library/ISO/game.iso','memstick/PSP/SAVEDATA/SAVE1/data.bin',
                    'memstick/PSP/TEXTURES/ULJM05558/textures.zip',
                    'xmb-assets/preview.png'):
            self.assertEqual((target/rel).read_bytes(),(self.old/rel).read_bytes())

    def test_refuses_overwrite_existing_and_overlapping_root(self):
        for target in (self.old,self.old/'memstick',self.root):
            with self.subTest(target=target),self.assertRaises(StorageLocationError):
                self.migrate(target)
        target=self.root/'already-created'
        target.mkdir()
        with self.assertRaisesRegex(StorageLocationError,'carpeta nueva'):
            self.migrate(target)

    def test_source_symlink_must_not_be_followed(self):
        link=self.old/'library/ISO/symlink.iso'
        try:
            link.symlink_to(self.old/'library/ISO/game.iso')
        except (OSError,NotImplementedError):
            self.skipTest('Platform cannot create symlinks')
        with self.assertRaises(StorageLocationError):
            self.migrate(self.root/'dest')
        self.assertFalse((self.root/'dest').exists())

    def test_native_folder_picker_returns_a_new_child_without_creating_it(self):
        if __import__('os').name!='nt':
            self.skipTest('Native folder picker requires Windows')
        from types import SimpleNamespace
        seen=[]
        def fake_run(args,**kwargs):
            seen.append((args,kwargs))
            return SimpleNamespace(returncode=0,stdout='PSPDOCK-FOLDER:'+str(self.root)+'\n',stderr='')
        selected=choose_pc_storage_parent(run=fake_run)
        self.assertEqual(selected,str(self.root/'PSP Dock'))
        self.assertFalse((self.root/'PSP Dock').exists())
        self.assertIn('-STA',seen[0][0])
        self.assertIn('FolderBrowserDialog',seen[0][0][-1])
        def cancelled(*args,**kwargs):
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        self.assertIsNone(choose_pc_storage_parent(run=cancelled))

    def test_retro_sram_profiles_and_backups_follow_relocation_twice(self):
        for folder,name in [('retro-saves/volume-a','game.srm'),
                            ('retro-states/volume-a','game.state'),
                            ('retro-profiles/autoconfig/dinput','PSP Dock HID.cfg'),
                            ('retro-save-backups','original.bak')]:
            dest=self.old/folder/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(b'RETRO_FICTITIOUS')
        first=self.root/'relocated-once'
        self.migrate(first)
        for file in ('retro-saves/volume-a/game.srm','retro-states/volume-a/game.state',
                     'retro-profiles/autoconfig/dinput/PSP Dock HID.cfg',
                     'retro-save-backups/original.bak'):
            self.assertEqual((first/file).read_bytes(),b'RETRO_FICTITIOUS')
        second=self.root/'relocated-twice'
        migrate_pc_data(target=second,library=first/'library',
                        memstick=first/'memstick',old_data=self.old,old_storage=first)
        self.assertEqual((second/'retro-saves/volume-a/game.srm').read_bytes(),b'RETRO_FICTITIOUS')
        self.assertEqual((first/'retro-saves/volume-a/game.srm').read_bytes(),b'RETRO_FICTITIOUS')


if __name__=='__main__':
    unittest.main()
