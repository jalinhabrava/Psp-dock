"""End-to-end synthetic SRAM review, conflict and return; never use real USB."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock
from psp_dock.emulation.retro_runtime import private_save_paths


class PSPRetroSRAMFlow(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.psp=self.base/'synthetic-psp'
        homebrew=self.psp/'PSP/GAME/gpSP'
        (homebrew/'ROMS').mkdir(parents=True)
        (homebrew/'SAVES').mkdir()
        (homebrew/'EBOOT.PBP').write_bytes(b'synthetic')
        (homebrew/'ROMS/Golden.gba').write_bytes(b'synthetic rom')
        self.psp_sram=homebrew/'SAVES/Golden.sav'
        self.psp_sram.write_bytes(b'a'*32768)
        (self.psp/'PSP/SAVEDATA').mkdir()
        self.usb=patch('dock.windows_devices',return_value=[])
        self.usb.start()
        self.addCleanup(self.usb.stop)
        self.dock=Dock(self.base/'data')
        # Isolate SRAM regression tests from the real PC-only downloader.
        self.dock.settings({'psp_root':str(self.psp),'retro_install_policy':'off'})
        self.rom=self.dock.retro_catalog()['roms'][0]

    def rows(self):
        return self.dock.retro_save_plan()['rows']

    def row(self):
        rows=self.rows()
        self.assertEqual(len(rows),1)
        return rows[0]

    def copy_choice(self,choice):
        row=self.row()
        return self.dock.sync_retro_save(self.rom['id'],choice,row['revision'])

    def pc_sram(self):
        return private_save_paths(self.dock.config['storage_root'],
                                  self.dock.retro_volume,self.rom['id'])[0]

    def prepare_return(self):
        self.dock.transfer_retro([self.rom['id']])
        self.dock.session['prepared_volume']=self.dock.retro_volume
        self.dock.session.update(running=False,pending_return=True,exit_requested=True,
             return_sync_authorized=True,return_sync_attempted=False,
             close_after_verified_return=True)

    def check_return(self):
        with patch('psp_dock.devices.bridge_hid.probe',return_value={'supported':True,'errors':[], 'devices':[]}):
            return self.dock.complete_authorized_return()

    def test_import_requires_explicit_consent_and_is_bidirectional(self):
        self.assertEqual(self.row()['status'],'psp_to_pc')
        self.assertFalse(self.pc_sram().exists())
        result=self.copy_choice('psp')
        self.assertEqual(result['pending'],0)
        self.assertEqual(self.pc_sram().read_bytes(),b'a'*32768)
        self.pc_sram().write_bytes(b'b'*32768)
        self.assertEqual(self.row()['status'],'pc_to_psp')
        self.copy_choice('pc')
        self.assertEqual(self.psp_sram.read_bytes(),b'b'*32768)
        self.assertEqual(self.row()['status'],'identical')
        self.assertTrue(list((self.base/'data/retro-save-backups').rglob('*.bak')))

    def test_identical_sram_establishes_baseline_without_copying_anything(self):
        pc=self.pc_sram()
        pc.parent.mkdir(parents=True)
        pc.write_bytes(self.psp_sram.read_bytes())
        self.assertEqual(self.row()['status'],'identical')
        self.assertEqual(self.dock.retro_save_plan()['pending'],0)
        pc.write_bytes(b'z'*32768)
        self.assertEqual(self.row()['status'],'pc_to_psp')
        self.assertEqual(self.psp_sram.read_bytes(),b'a'*32768)

    def test_stale_revision_and_wrong_volume_never_write_psp(self):
        self.copy_choice('psp')
        self.pc_sram().write_bytes(b'b'*32768)
        stale=self.row()['revision']
        self.pc_sram().write_bytes(b'c'*32768)
        with self.assertRaisesRegex(ValueError,'cambiado'):
            self.dock.sync_retro_save(self.rom['id'],'pc',stale)
        self.dock.session['prepared_volume']='unknown-volume'
        with self.assertRaisesRegex(ValueError,'Memory Stick'):
            self.copy_choice('pc')
        self.assertEqual(self.psp_sram.read_bytes(),b'a'*32768)

    def test_retro_launch_needs_preimport_even_if_rom_is_ready(self):
        self.dock.transfer_retro([self.rom['id']])
        self.dock.session['prepared_volume']=self.dock.retro_volume
        self.dock.retro_save_plan()
        with patch.object(self.dock,'root',return_value=None),\
             patch('dock.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'Revisa la partida retro'):
                self.dock.launch_retro(self.rom['id'])
        spawn.assert_not_called()

    def test_return_waits_for_review_and_explicit_sync_then_completes(self):
        self.copy_choice('psp')
        self.prepare_return()
        self.pc_sram().write_bytes(b'z'*32768)
        self.assertIsNone(self.check_return())
        self.assertEqual(self.dock.session['return_result'],'retro_review')
        self.assertTrue(self.dock.session['return_sync_authorized'])
        self.assertEqual(self.psp_sram.read_bytes(),b'a'*32768)
        with self.assertRaisesRegex(ValueError,'partidas Retro'):
            self.dock.sync()
        self.copy_choice('pc')
        final=self.check_return()
        self.assertIsNotNone(final)
        self.assertEqual(self.dock.session['return_result'],'verified')
        self.assertEqual(self.psp_sram.read_bytes(),b'z'*32768)

    def test_skip_retains_both_versions_and_allows_return(self):
        self.copy_choice('psp')
        self.prepare_return()
        self.pc_sram().write_bytes(b'x'*32768)
        self.assertIsNone(self.check_return())
        status=self.copy_choice('skip')
        self.assertEqual(status['pending'],0)
        self.assertTrue(self.row()['skipped'])
        self.assertEqual(self.psp_sram.read_bytes(),b'a'*32768)
        self.assertIsNotNone(self.check_return())
        self.assertEqual(self.pc_sram().read_bytes(),b'x'*32768)

    def test_skip_without_exit_refused(self):
        with self.assertRaisesRegex(ValueError,'devolver'):
            self.copy_choice('skip')

    def test_conflict_manual_choice_creates_backup(self):
        self.copy_choice('psp')
        self.psp_sram.write_bytes(b'c'*32768)
        self.pc_sram().write_bytes(b'd'*32768)
        self.assertEqual(self.row()['status'],'conflict')
        self.copy_choice('psp')
        self.assertEqual(self.pc_sram().read_bytes(),b'c'*32768)
        self.assertEqual(self.row()['status'],'identical')


if __name__=='__main__':
    unittest.main()
