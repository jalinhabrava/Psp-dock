"""PSP lab cleanup is tightly scoped, independently backed up and reversible."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from psp_dock_consolidate import (
    LABS, PLUGINS, PRODUCT, CleanupError, clean_plugin_config,
    consolidate, inspect, verify_cleanup_backup,
)


class ConsolidatePSPLabsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.psp=self.home/'psp-fixture'
        self.product=self.home/'product/dist'
        self.product.mkdir(parents=True)
        (self.psp/'PSP/SAVEDATA/DARIUS/SAVE.BIN').parent.mkdir(parents=True)
        (self.psp/'PSP/SAVEDATA/DARIUS/SAVE.BIN').write_bytes(b'real unique save fixture')
        (self.psp/'PSP/SYSTEM').mkdir(parents=True)
        (self.psp/'PSP/SYSTEM/psp.cfg').write_bytes(b'configuration fixture')
        (self.psp/'ISO').mkdir()
        (self.psp/'ISO/MY_GAME.iso').write_bytes(b'user owned ISO fixture')
        (self.psp/'PSP/GAME/ARK').mkdir(parents=True)
        (self.psp/'PSP/GAME/ARK/EBOOT.PBP').write_bytes(b'ARK must survive')
        (self.psp/'PSP/GAME/OtherHomebrew').mkdir()
        (self.psp/'PSP/GAME/OtherHomebrew/EBOOT.PBP').write_bytes(b'user owned')
        for group, parent in ((LABS,'PSP/GAME'),(PLUGINS,'seplugins')):
            for dirname, files in group.items():
                directory=self.psp/parent/dirname
                directory.mkdir(parents=True, exist_ok=True)
                for name in files:
                    (directory/name).write_bytes((dirname+'|'+name).encode()*3)
        self.config=self.psp/'seplugins/PLUGINS.TXT'
        self.old_config=(
            'vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n'
            'vsh, ms0:/SEPLUGINS/PSPDockKernelK1/pspdockk1observer.prx, on\n'
            'vsh, ms0:/SEPLUGINS/PSPDockK1Proof/pspdockk1proof.prx, off\n'
        ).encode()
        self.config.write_bytes(self.old_config)
        self.bridge = Path(__file__).resolve().parents[1] / (
            'homebrew/psp-dock-app/dist/bridgehid.prx')
        self.assertTrue(self.bridge.is_file())
        for name in PRODUCT:
            dest=self.product/name
            if name=='bridgehid.prx':
                dest.write_bytes(self.bridge.read_bytes())
            else:
                dest.write_bytes((name+' PRODUCT').encode()*8)
        self.ident=SimpleNamespace(
            volume_serial='2958455453',
            usb_device_id='USB\\VID_054C&PID_02D2\\SAFE_TEST')
        self.backups=self.home/'PCbackups'

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_foreign_plugin_entry_is_removed(self):
        foreign=b'vsh, ms0:/SEPLUGINS/OtherPlugin/prx.prx, on\n'
        self.assertEqual(clean_plugin_config(self.old_config+foreign),foreign)
        with self.assertRaisesRegex(CleanupError,'desconocida'):
            clean_plugin_config(
                self.old_config+b'vsh, ms0:/SEPLUGINS/PSPDockUnknown/plugin.prx, on\n'
            )

    def test_read_only_inspection_never_modifies_psp(self):
        before=self.config.read_bytes()
        info=inspect(self.psp,self.product)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertEqual(len(info['legacy_dirs']),12)
        self.assertEqual(len(info['sources']),4)
        self.assertEqual((self.psp/'PSP/SAVEDATA/DARIUS/SAVE.BIN').read_bytes(),
                         b'real unique save fixture')

    def test_full_consolidation_preserves_user_data_and_verifies_backups(self):
        saved=self.psp/'PSP/SAVEDATA/DARIUS/SAVE.BIN'
        original=saved.read_bytes()
        result=consolidate(
            self.psp,self.product,self.backups,self.ident,lambda:True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['removed_lab_folders'],12)
        self.assertEqual(result['plugins_txt_entries_removed'],3)
        self.assertEqual(result['savedata_files_verified_unchanged'],1)
        self.assertEqual(saved.read_bytes(),original)
        self.assertEqual((self.psp/'ISO/MY_GAME.iso').read_bytes(),
                         b'user owned ISO fixture')
        self.assertEqual((self.psp/'PSP/GAME/ARK/EBOOT.PBP').read_bytes(),
                         b'ARK must survive')
        self.assertEqual((self.psp/'PSP/GAME/OtherHomebrew/EBOOT.PBP').read_bytes(),
                         b'user owned')
        self.assertEqual(self.config.read_bytes(),b'')
        for name in LABS:
            self.assertFalse((self.psp/'PSP/GAME'/name).exists())
        for name in PLUGINS:
            self.assertFalse((self.psp/'seplugins'/name).exists())
        for name in PRODUCT:
            self.assertEqual((self.psp/'PSP/GAME/PSPDock'/name).read_bytes(),
                             (self.product/name).read_bytes())
        from psp_backup import verify_archive
        verify_archive(Path(result['critical_backup']))
        archive=verify_cleanup_backup(Path(result['legacy_backup']))
        self.assertEqual(archive['status'],'verified')
        self.assertEqual(archive['files'],result['removed_lab_files']+1)
        manifest=json.loads(
            (Path(result['legacy_backup'])/'manifest.json').read_text())
        self.assertEqual(manifest['psp_volume_serial'],'2958455453')

    def test_unknown_file_refuses_cleanup_before_writes(self):
        path=self.psp/'PSP/GAME/PSPDockHidOnly/NEW_SAVE_NOT_OURS.dat'
        path.write_bytes(b'user file cannot be destroyed')
        with self.assertRaisesRegex(CleanupError,'no previstos'):
            inspect(self.psp,self.product)
        self.assertTrue(path.exists())
        self.assertFalse((self.psp/'PSP/GAME/PSPDock').exists())
        self.assertEqual(self.config.read_bytes(),self.old_config)

    def test_identity_failure_never_installs_or_removes(self):
        with self.assertRaisesRegex(CleanupError,'Sony cambió'):
            consolidate(self.psp,self.product,self.backups,self.ident,
                        lambda:False)
        self.assertTrue((self.psp/'PSP/GAME/PSPDockHidOnly').is_dir())
        self.assertFalse((self.psp/'PSP/GAME/PSPDock').exists())

    def test_replacement_of_existing_dock_not_permitted(self):
        (self.psp/'PSP/GAME/PSPDock').mkdir()
        with self.assertRaisesRegex(CleanupError,'existe PSP Dock'):
            inspect(self.psp,self.product)

    def test_unverified_driver_is_rejected(self):
        (self.product/'bridgehid.prx').write_bytes(b'UNSAFE DRIVER')
        with self.assertRaisesRegex(CleanupError,'difiere'):
            inspect(self.psp,self.product)


if __name__=='__main__':
    unittest.main()
