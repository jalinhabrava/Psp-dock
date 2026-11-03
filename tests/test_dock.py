import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dock import (Dock, copy_game, copy_slot, digest, game_inventory, inside,
                  sync_plan, tree_signature, prepare_emulator,
                  acquire_instance_lock, require_psp_identity_before_writing)

class Saves(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.psp, self.pc = self.root/'psp', self.root/'pc'
        self.psp.mkdir()
        self.pc.mkdir()
    def tearDown(self):
        self.temp.cleanup()
    def save(self, root, value, slot='GAME01'):
        p = root/slot/'DATA.BIN'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value)
        return p
    def test_initial_import_export_and_conflict(self):
        self.save(self.psp, b'psp')
        plan = sync_plan(self.psp,self.pc,{})
        self.assertEqual(plan['actions'][0]['direction'],'to_pc')
        self.save(self.pc,b'pc')
        self.assertEqual(sync_plan(self.psp,self.pc,{})['conflicts'][0]['slot'],'GAME01')
    def test_three_way_and_whole_slot_conflicts(self):
        self.save(self.psp,b'base')
        self.save(self.pc,b'base')
        baseline = {'GAME01':tree_signature(self.psp/'GAME01')}
        self.save(self.pc,b'changed')
        self.assertEqual(sync_plan(self.psp,self.pc,baseline)['actions'][0]['direction'],'to_psp')
        (self.psp/'GAME01/ICON.PNG').write_bytes(b'different file changed')
        self.assertTrue(sync_plan(self.psp,self.pc,baseline)['conflicts'])
    def test_deletion_not_propagated(self):
        self.save(self.pc,b'base')
        baseline = {'GAME01':tree_signature(self.pc/'GAME01')}
        plan = sync_plan(self.psp,self.pc,baseline)
        self.assertFalse(plan['actions'])
        self.assertTrue(plan['conflicts'])
    def test_verified_copy_and_backup(self):
        self.save(self.psp,b'new')
        self.save(self.pc,b'old')
        copy_slot(self.psp/'GAME01', self.pc/'GAME01', self.root/'backup',
                  tree_signature(self.psp/'GAME01'),tree_signature(self.pc/'GAME01'))
        self.assertEqual((self.pc/'GAME01/DATA.BIN').read_bytes(),b'new')
        self.assertEqual(next((self.root/'backup').glob('*/DATA.BIN')).read_bytes(),b'old')
        self.assertFalse(list(self.pc.glob('.dock-*')))
    def test_changed_target_precondition(self):
        self.save(self.psp,b'new')
        self.save(self.pc,b'old')
        old = tree_signature(self.pc/'GAME01')
        self.save(self.pc,b'concurrent')
        with self.assertRaises(ValueError):
            copy_slot(self.psp/'GAME01',self.pc/'GAME01',self.root/'backup',tree_signature(self.psp/'GAME01'),old)
        self.assertEqual((self.pc/'GAME01/DATA.BIN').read_bytes(),b'concurrent')
    def test_rename_failure_preserves_original_and_journal(self):
        self.save(self.psp,b'new')
        self.save(self.pc,b'old')
        real = os.replace
        def fail_stage(src,dst):
            if Path(src).name.startswith('.dock-stage-'):
                raise OSError('simulated unplug')
            return real(src,dst)
        with patch('dock.os.replace',side_effect=fail_stage):
            with self.assertRaises(OSError):
                copy_slot(self.psp/'GAME01',self.pc/'GAME01',self.root/'backup',tree_signature(self.psp/'GAME01'),tree_signature(self.pc/'GAME01'))
        self.assertEqual((self.pc/'GAME01/DATA.BIN').read_bytes(),b'old')
        self.assertTrue(list(self.pc.glob('.dock-journal-*')))
    def test_no_delete_by_conflict_resolution(self):
        self.save(self.psp,b'new')
        with self.assertRaises(ValueError):
            copy_slot(self.pc/'GAME01',self.psp/'GAME01',self.root/'backup',None,tree_signature(self.psp/'GAME01'))

class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.psp=self.root/'console'
        (self.psp/'PSP/SAVEDATA/GAME01').mkdir(parents=True)
        (self.psp/'PSP/SAVEDATA/GAME01/DATA.BIN').write_bytes(b'initial')
        self.dock=Dock(self.root/'data')
        self.dock.settings({'psp_root':str(self.psp)})
    def tearDown(self):
        self.temp.cleanup()
    def test_roundtrip_and_backups(self):
        self.dock.sync()
        pc=Path(self.dock.config['memstick'])/'PSP/SAVEDATA/GAME01/DATA.BIN'
        self.assertEqual(pc.read_bytes(),b'initial')
        pc.write_bytes(b'played on PC')
        self.dock.sync()
        self.assertEqual((self.psp/'PSP/SAVEDATA/GAME01/DATA.BIN').read_bytes(),b'played on PC')
        self.assertFalse(self.dock.save_plan()['actions'])
    def test_explicit_conflict_and_choice(self):
        self.dock.sync()
        (self.psp/'PSP/SAVEDATA/GAME01/DATA.BIN').write_bytes(b'PSP version')
        pc=Path(self.dock.config['memstick'])/'PSP/SAVEDATA/GAME01/DATA.BIN'
        pc.write_bytes(b'PC version')
        self.assertTrue(self.dock.sync()['conflicts'])
        self.assertEqual(pc.read_bytes(),b'PC version')
        self.dock.sync({'slot':'GAME01','choice':'psp'})
        self.assertEqual(pc.read_bytes(),b'PSP version')

    def test_mixed_save_conflicts_stop_all_automatic_copies(self):
        self.dock.sync()
        pc = Path(self.dock.config['memstick'])/'PSP/SAVEDATA/GAME01/DATA.BIN'
        pc.write_bytes(b'PC edition')
        (self.psp/'PSP/SAVEDATA/GAME01/DATA.BIN').write_bytes(b'PSP edition')
        (self.psp/'PSP/SAVEDATA/NEW01').mkdir()
        (self.psp/'PSP/SAVEDATA/NEW01/DATA.BIN').write_bytes(b'new PSP save')
        plan = self.dock.sync()
        self.assertTrue(plan['actions'])
        self.assertTrue(plan['conflicts'])
        self.assertFalse((Path(self.dock.config['memstick']) /
                          'PSP/SAVEDATA/NEW01').exists())
        self.assertEqual(pc.read_bytes(), b'PC edition')
        # Resolving only the selected slot never erases the other one.
        self.dock.sync({'slot':'GAME01','choice':'pc'})
        self.assertEqual((self.psp/'PSP/SAVEDATA/GAME01/DATA.BIN').read_bytes(),b'PC edition')
        self.assertFalse((Path(self.dock.config['memstick']) /
                          'PSP/SAVEDATA/NEW01').exists())
    def test_other_volume_cannot_receive_pending_return(self):
        self.dock.session.update(pending_return=True,prepared_volume='other-volume')
        with self.assertRaises(ValueError):
            self.dock.sync()
    def test_journal_blocks_even_if_saves_identical(self):
        self.dock.sync()
        (self.psp/'PSP/SAVEDATA/.dock-journal-test.json').write_text('{}')
        with self.assertRaises(ValueError):
            self.dock.sync()
    def test_running_blocks_writes(self):
        self.dock.recovery_required=True
        with self.assertRaises(ValueError):
            self.dock.sync()
    def test_inventory_transfer_dependencies_and_no_overwrite(self):
        (self.psp/'ISO').mkdir()
        (self.psp/'ISO/title.ISO').write_bytes(b'not an actual game')
        (self.psp/'ISO/other.cso').write_bytes(b'compressed fixture')
        game=self.psp/'PSP/GAME/Homebrew'
        game.mkdir(parents=True)
        (game/'EBOOT.PBP').write_bytes(b'fixture')
        (game/'assets').mkdir()
        (game/'assets/data.bin').write_bytes(b'dependency')
        games=self.dock.games()
        self.assertEqual({g['format'] for g in games},{'ISO','CSO','PBP'})
        self.dock.transfer([g['id'] for g in games])
        self.assertTrue(all(g['availability']=='ready' for g in self.dock.games()))
        self.assertEqual((Path(self.dock.config['library'])/'PSP/GAME/Homebrew/assets/data.bin').read_bytes(),b'dependency')
        with self.assertRaises(ValueError):
            self.dock.transfer([games[0]['id']])
    def test_demo_launch_rejected(self):
        self.dock.demo=True
        with self.assertRaises(ValueError):
            self.dock.launch('anything')
    def test_identical_game_with_another_filename_is_not_copied(self):
        (self.psp/'ISO').mkdir()
        (self.psp/'ISO/PSP title.iso').write_bytes(b'identical content')
        local=Path(self.dock.config['library'])/'ISO/PC title.iso'
        local.parent.mkdir(parents=True)
        local.write_bytes(b'identical content')
        game=self.dock.games()[0]
        self.assertEqual(game['availability'],'ready')
        self.assertEqual(game['local_relative'],'ISO/PC title.iso')
    def test_empty_library_does_not_hash_usb_games(self):
        (self.psp/'ISO').mkdir()
        (self.psp/'ISO/large.iso').write_bytes(b'fixture')
        with patch('dock.digest',side_effect=AssertionError('Must not read missing games')):
            self.assertEqual(self.dock.games()[0]['availability'],'missing')

    def test_reconnect_and_restart_reuse_unchanged_usb_game_signature(self):
        (self.psp/'ISO').mkdir()
        source=self.psp/'ISO/game.iso'
        source.write_bytes(b'unchanged original game')
        target=Path(self.dock.config['library'])/'ISO/game.iso'
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        self.assertEqual(self.dock.games()[0]['availability'],'ready')
        restarted=Dock(self.root/'data')
        original_digest=digest
        def no_usb_rehash(path):
            if Path(path).is_relative_to(self.psp):
                raise AssertionError('Unchanged PSP game was read again')
            return original_digest(path)
        with patch('dock.digest', side_effect=no_usb_rehash):
            self.assertEqual(restarted.games()[0]['availability'],'ready')
        source.write_bytes(b'changed original game')
        self.assertEqual(restarted.games()[0]['availability'],'different')

    def test_homebrew_dependency_change_invalidates_tree_signature(self):
        folder=self.psp/'PSP/GAME/MyHomebrew'
        folder.mkdir(parents=True)
        (folder/'EBOOT.PBP').write_bytes(b'homebrew')
        asset=folder/'data.bin'
        asset.write_bytes(b'first asset')
        local=Path(self.dock.config['library'])/'PSP/GAME/MyHomebrew'
        local.mkdir(parents=True)
        (local/'EBOOT.PBP').write_bytes(b'homebrew')
        (local/'data.bin').write_bytes(b'first asset')
        self.assertEqual(self.dock.games()[0]['availability'],'ready')
        self.assertEqual(Dock(self.root/'data').games()[0]['availability'],'ready')
        asset.write_bytes(b'changed asset bytes')
        self.assertEqual(Dock(self.root/'data').games()[0]['availability'],'different')
    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            self.dock.settings({'library':str(self.psp/'ISO')})
    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            inside(self.psp,'../escape')
    def test_existing_game_never_overwritten(self):
        source,target=self.root/'source.iso',self.root/'target.iso'
        source.write_bytes(b'new')
        target.write_bytes(b'old')
        with self.assertRaises(ValueError):
            copy_game(source,target)
        self.assertEqual(target.read_bytes(),b'old')
    def test_autodetected_overlap_rejected_at_runtime(self):
        self.dock.config['psp_root']=''
        self.dock.config['memstick']=str(self.psp)
        with patch.object(self.dock,'root',return_value=self.psp):
            with self.assertRaises(ValueError):
                self.dock.sync()
    def test_changed_paths_invalidate_preparation(self):
        self.dock.sync()
        self.assertIn('prepared_volume',self.dock.session)
        self.dock.settings({'memstick':str(self.root/'another-memstick')})
        self.assertNotIn('prepared_volume',self.dock.session)
    def test_disconnected_launch_blocks_local_journal(self):
        self.dock.sync()
        saves=Path(self.dock.config['memstick'])/'PSP/SAVEDATA'
        (saves/'.dock-journal-test.json').write_text('{}')
        with patch.object(self.dock,'root',return_value=None):
            with self.assertRaisesRegex(ValueError,'interrumpida'):
                self.dock.launch('unused')
    def test_portable_runtime_preserves_installation(self):
        source=self.root/'ppsspp'
        source.mkdir()
        exe=source/'PPSSPPWindows64.exe'
        exe.write_bytes(b'Synthetic EXE never executed')
        (source/'assets/flash0').mkdir(parents=True)
        (source/'assets/flash0/test.bin').write_bytes(b'Synthetic asset')
        (source/'installed.txt').write_text('ORIGINAL PATH')
        memory=self.root/'private-memory'
        result=prepare_emulator(exe,self.dock.data,memory,3,'directx11')
        self.assertEqual(result.read_bytes(),exe.read_bytes())
        self.assertEqual((source/'installed.txt').read_text(),'ORIGINAL PATH')
        self.assertEqual((result.parent/'installed.txt').read_text().strip(),str(memory))
        self.assertIn('InternalResolution = 3',(memory/'PSP/SYSTEM/ppsspp.ini').read_text())
    def test_second_instance_lock_rejected(self):
        handle=acquire_instance_lock(self.dock.data)
        try:
            with self.assertRaises(ValueError):
                acquire_instance_lock(self.dock.data)
        finally:
            handle.close()

if __name__=='__main__':
    unittest.main()


@unittest.skipUnless(os.name == 'nt', 'Windows physical USB drive identity')
class PhysicalStorageIdentityTests(unittest.TestCase):
    def test_unknown_removable_volume_never_authorizes_writes(self):
        from psp_usb import PspStorage, UsbTransitionError
        anchor = Path('Q:/')
        with patch('dock.windows_drive_type',return_value=2), \
             patch('psp_usb.identify',
                   side_effect=UsbTransitionError('not a real PSP')) as verify:
            with self.assertRaisesRegex(ValueError, 'no tiene identidad Sony'):
                require_psp_identity_before_writing(anchor)
            verify.assert_called_once()

    def test_wrong_sony_pid_or_volume_serial_blocks(self):
        from psp_usb import PspStorage
        anchor = Path('Q:/')
        for usb, serial in (
                ('USB\\VID_054C&PID_1234\\OtherSony', '123'),
                ('USB\\VID_054C&PID_02D2\\PSP', 'different'),
        ):
            with self.subTest(usb=usb, serial=serial), \
                 patch('dock.windows_drive_type',return_value=2), \
                 patch('dock.volume_identity',return_value='123'), \
                 patch('psp_usb.identify',return_value=(
                     PspStorage('Q:\\',serial,'usb-sony',usb,'PSP'),0)):
                with self.assertRaisesRegex(ValueError, 'no coincide'):
                    require_psp_identity_before_writing(anchor)

    def test_exact_sony_psp_usb_and_volume_identity_required(self):
        from psp_usb import PspStorage
        anchor = Path('Q:/')
        with patch('dock.windows_drive_type',return_value=2), \
             patch('dock.volume_identity',return_value='123'), \
             patch('psp_usb.identify',return_value=(
                 PspStorage('Q:\\','123','USBSTOR\\DISK&VEN_SONY&PROD_PSP',
                            'USB\\VID_054C&PID_02D2\\PSP','PSP'),0)):
            self.assertIsNone(require_psp_identity_before_writing(anchor))
