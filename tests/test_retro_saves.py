"""Raw SRAM reconciliation under exact matching PSP emulator folders."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from retro_saves import (plan_retro_saves, copy_sram_verified,
                         _baseline_key, _sha)
from retro_runtime import private_save_paths


class SaveContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.psp=self.root/'psp'
        self.pc=self.root/'pc'
        self.folder=self.psp/'PSP/GAME/gpSP'
        self.folder.mkdir(parents=True)
        (self.folder/'ROMS').mkdir()
        (self.folder/'SAVES').mkdir()
        self.rom=self.folder/'ROMS/Golden.gba'
        self.rom.write_bytes(b'synthetic fixture')
        self.psp_save=self.folder/'SAVES/Golden.sav'
        self.volume='PSP fixture volume'
        self.rom_id='a'*24
        self.emulators=[{'id':'b'*24,'relative':'PSP/GAME/gpSP/EBOOT.PBP',
                         'name':'gpSP Kai','systems':['gba']}]
        self.roms=[{'id':self.rom_id,'relative':'PSP/GAME/gpSP/ROMS/Golden.gba',
                    'title':'Golden','format':'GBA','system':'gba','emulator_id':'b'*24}]
        self.baselines={}

    def write_psp(self,byte=b'a'):
        self.psp_save.write_bytes(byte*32768)

    def plan(self):
        return plan_retro_saves(root=self.psp,storage_root=self.pc,volume=self.volume,
                  roms=self.roms,emulators=self.emulators,baselines=self.baselines)[0]

    def pc_save(self):
        return private_save_paths(self.pc,self.volume,self.rom_id)[0]

    def copy(self,choice,row):
        pc=self.pc_save()
        if choice=='psp':
            source,target,source_root,target_root=self.psp_save,pc,self.psp,self.pc
            src_hash,dst_hash=_sha(source),_sha(target)
        else:
            source,target,source_root,target_root=pc,self.psp_save,self.pc,self.psp
            src_hash,dst_hash=_sha(source),_sha(target)
        result=copy_sram_verified(
            source=source,target=target,source_root=source_root,
            target_root=target_root,storage_root=self.pc,
            backup_dir=self.pc/'retro-save-backups',journal_dir=self.pc/'retro-save-journals',
            expected_source=src_hash,expected_target=dst_hash,system='gba')
        self.baselines[_baseline_key(self.volume,self.rom_id)]=result

    def test_import_update_export_with_clean_three_way_baseline(self):
        self.assertEqual(self.plan()['status'],'none')
        self.write_psp()
        row=self.plan()
        self.assertEqual(row['status'],'psp_to_pc')
        self.assertEqual(row['source_relative'],'PSP/GAME/gpSP/SAVES/Golden.sav')
        self.copy('psp',row)
        self.assertEqual(self.plan()['status'],'identical')
        self.pc_save().write_bytes(b'b'*32768)
        self.assertEqual(self.plan()['status'],'pc_to_psp')
        self.copy('pc',self.plan())
        self.assertEqual(self.plan()['status'],'identical')
        self.assertEqual(self.psp_save.read_bytes(),b'b'*32768)
        self.assertTrue(list((self.pc/'retro-save-backups').rglob('*.bak')))
        self.assertFalse(list((self.pc/'retro-save-journals').glob('*.json')))

    def test_conflict_and_precondition_reject_stale_copy(self):
        self.write_psp()
        pc=self.pc_save()
        pc.parent.mkdir(parents=True)
        pc.write_bytes(b'b'*32768)
        row=self.plan()
        self.assertEqual(row['status'],'conflict')
        stale=row['revision']
        pc.write_bytes(b'c'*32768)
        self.assertNotEqual(self.plan()['revision'],stale)
        with self.assertRaises(ValueError):
            copy_sram_verified(source=self.psp_save,target=pc,
                source_root=self.psp,target_root=self.pc,storage_root=self.pc,
                backup_dir=self.pc/'retro-save-backups',journal_dir=self.pc/'retro-save-journals',
                expected_source=_sha(self.psp_save),expected_target='e'*64,system='gba')
        self.assertEqual(pc.read_bytes(),b'c'*32768)

    def test_duplicate_psp_sram_refuses_guess(self):
        self.write_psp()
        (self.folder/'ROMS/Golden.sav').write_bytes(b'a'*32768)
        self.assertEqual(self.plan()['status'],'blocked')

    def test_two_roms_with_same_stem_are_not_assigned_same_sram(self):
        other=self.folder/'EXTRA/Golden.gba'
        other.parent.mkdir()
        other.write_bytes(b'other fixture')
        self.roms.append({**self.roms[0],'id':'c'*24,
                          'relative':'PSP/GAME/gpSP/EXTRA/Golden.gba'})
        self.write_psp()
        rows=plan_retro_saves(root=self.psp,storage_root=self.pc,
            volume=self.volume,roms=self.roms,emulators=self.emulators,
            baselines=self.baselines)
        self.assertTrue(all(r['status']=='blocked' for r in rows))

    def test_invalid_or_compressed_save_does_not_sync(self):
        self.psp_save.write_bytes(b'header-not-raw')
        self.assertEqual(self.plan()['status'],'blocked')
        self.psp_save.write_bytes(b'a'*33280)  # SNES-style 512-byte header, not GBA SRAM
        self.assertEqual(self.plan()['status'],'blocked')

    def test_zip_and_other_emulators_remain_unmanaged(self):
        self.roms[0]['format']='ZIP'
        self.assertEqual(self.plan()['status'],'unsupported')
        self.roms[0]['format']='GBA'
        self.emulators[0]['name']='DaedalusX64'
        self.assertEqual(self.plan()['status'],'unsupported')

    def test_missing_psp_save_requires_explicit_suggested_path(self):
        pc=self.pc_save()
        pc.parent.mkdir(parents=True)
        pc.write_bytes(b'a'*32768)
        row=self.plan()
        self.assertEqual(row['status'],'pc_to_psp')
        self.assertTrue(row['suggested_path'])
        self.copy('pc',row)
        self.assertEqual(self.plan()['status'],'identical')

    def test_real_uo_gpsp_gba_rom_and_gba_sav_layout(self):
        # Reproduces the physical PSP directory structure observed without
        # reading or altering the real save data.
        romdir=self.folder/'GBA/ROM'
        savdir=self.folder/'GBA/SAV'
        romdir.mkdir(parents=True)
        savdir.mkdir(parents=True)
        (romdir/'Golden.gba').write_bytes(b'fake cartridge')
        self.roms[0]['relative']='PSP/GAME/gpSP/GBA/ROM/Golden.gba'
        target=savdir/'Golden.sav'
        target.write_bytes(b'g'*32768)
        row=self.plan()
        self.assertEqual(row['status'],'psp_to_pc')
        self.assertEqual(row['source_relative'],'PSP/GAME/gpSP/GBA/SAV/Golden.sav')
        target.unlink()
        self.assertTrue(self.plan()['suggested_path'])
        self.assertEqual(self.plan()['source_relative'],'PSP/GAME/gpSP/GBA/SAV/Golden.sav')

    def test_snes_names_must_match_exactly_no_fuzzy_overwrite(self):
        self.emulators[0]['name']='Snes9xTYL'
        self.roms[0]['system']='snes'
        self.roms[0]['format']='SMC'
        self.roms[0]['relative']='PSP/GAME/gpSP/ROMS/Golden (USA).smc'
        (self.folder/'ROMS/Golden (USA).smc').write_bytes(b'fixture')
        (self.folder/'SAVES/Golden.srm').write_bytes(b'x'*8192)
        row=self.plan()
        self.assertEqual(row['status'],'none')
        self.assertTrue(row['suggested_path'])

    def test_interrupted_publish_preserves_psp_save_backup_and_journal(self):
        self.write_psp()
        pc=self.pc_save()
        pc.parent.mkdir(parents=True)
        pc.write_bytes(b'b'*32768)
        original=pc.read_bytes()
        with patch('retro_saves.os.replace',side_effect=OSError('simulated FAT failure')):
            with self.assertRaises(OSError):
                self.copy('psp',self.plan())
        self.assertEqual(pc.read_bytes(),original)
        self.assertTrue(list((self.pc/'retro-save-journals').glob('*.json')))
        self.assertTrue(list((self.pc/'retro-save-backups').glob('*.bak')))
        with self.assertRaisesRegex(ValueError,'interrumpida'):
            self.copy('psp',self.plan())

    def test_source_symlink_refused_even_if_filename_matches(self):
        outside=self.root/'outside.sav'
        outside.write_bytes(b'a'*32768)
        try:
            self.psp_save.symlink_to(outside)
        except (OSError,NotImplementedError):
            self.skipTest('No symlink permissions')
        with self.assertRaises(ValueError):
            self.plan()

    def test_snes9xtyl_raw_srm_supported_not_savestate(self):
        self.emulators[0]['name']='Snes9xTYL'
        self.roms[0]['system']='snes'
        self.roms[0]['format']='SFC'
        self.roms[0]['relative']='PSP/GAME/gpSP/ROMS/Golden.sfc'
        (self.folder/'ROMS/Golden.sfc').write_bytes(b'fixture')
        (self.folder/'SAVES/Golden.srm').write_bytes(b'b'*8192)
        (self.folder/'SAVES/Golden.state').write_bytes(b'x'*512)
        self.assertEqual(self.plan()['status'],'psp_to_pc')
        self.assertTrue(self.plan()['source_relative'].endswith('Golden.srm'))

if __name__=='__main__':
    unittest.main()
