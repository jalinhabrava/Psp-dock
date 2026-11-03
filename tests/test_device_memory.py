import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from device_memory import DeviceMemory, _hash
from dock import atomic_json


class DeviceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'psp'
        (self.root/'ISO').mkdir(parents=True)
        (self.root/'PSP/GAME').mkdir(parents=True)
        (self.root/'ISO/Game.iso').write_bytes(b'game')
        self.memory=DeviceMemory(self.base/'pc',atomic_json)
        self.identity={'usb_parent':'USB\\VID_054C&PID_01CD\\PSP','volume_serial':'123'}
        self.config={'library':'C:/games','memstick':'C:/saves'}

    def test_unchanged_catalog_survives_restart_without_reparsing(self):
        scan=Mock(return_value=[{'id':'game','title':'Game'}])
        self.memory.inventory(self.root,'123','games',scan)
        restarted=DeviceMemory(self.base/'pc',atomic_json)
        result=restarted.inventory(self.root,'123','games',scan)
        self.assertEqual(result,[{'id':'game','title':'Game'}])
        scan.assert_called_once()
        self.assertEqual(restarted.stats,{'hits':1,'misses':0})

    def test_add_change_remove_and_other_card_invalidate(self):
        scan=Mock(return_value=[])
        self.memory.inventory(self.root,'123','games',scan)
        file=self.root/'ISO/New.iso';file.write_bytes(b'new')
        self.memory.inventory(self.root,'123','games',scan)
        file.write_bytes(b'changed size')
        self.memory.inventory(self.root,'123','games',scan)
        file.unlink()
        self.memory.inventory(self.root,'123','games',scan)
        self.memory.inventory(self.root,'456','games',scan)
        self.assertEqual(scan.call_count,5)

    def test_preference_is_per_psp_card_and_pc_destination_and_opt_in(self):
        self.assertEqual(self.memory.preference(self.identity,self.config),{})
        self.memory.remember(self.identity,self.config,'saves',False)
        m=DeviceMemory(self.base/'pc',atomic_json)
        self.assertEqual(m.preference(self.identity,self.config),{'choice':'saves','automatic':False})
        self.assertEqual(m.preference({**self.identity,'volume_serial':'other'},self.config),{})
        self.assertEqual(m.preference({**self.identity,'usb_parent':'USB\\OTHER'},self.config),{})
        self.assertEqual(m.preference(self.identity,{**self.config,'memstick':'D:/saves'}),{})
        with self.assertRaises(ValueError):m.remember(self.identity,self.config,'all','yes')

    def test_corrupt_catalog_falls_back_to_scan(self):
        scan=Mock(return_value=[]);self.memory.inventory(self.root,'123','games',scan)
        file=next((self.base/'pc/catalog-cache').glob('*.json'))
        value=json.loads(file.read_text());value['data']=[{'invalid':True}]
        file.write_text(json.dumps(value))
        self.memory.inventory(self.root,'123','games',scan)
        self.assertEqual(scan.call_count,2)

    def test_old_valid_cache_remains_valid_when_metadata_is_unchanged(self):
        scan=Mock(return_value=[])
        self.memory.inventory(self.root,'123','games',scan)
        cache=next((self.base/'pc/catalog-cache').glob('*.json'))
        payload=json.loads(cache.read_text())
        payload['at']=1  # Age alone is not a content change.
        cache.write_text(json.dumps(payload))
        self.assertEqual(DeviceMemory(self.base/'pc',atomic_json).inventory(
            self.root,'123','games',scan),[])
        scan.assert_called_once()

    def test_same_memory_stick_reuses_catalog_after_drive_letter_changes(self):
        scan=Mock(return_value=[{'id':'game','title':'Game'}])
        self.memory.inventory(self.root,'123','games',scan)
        remounted=self.base/'different-drive-letter'
        shutil.copytree(self.root,remounted)
        fresh=DeviceMemory(self.base/'pc',atomic_json)
        self.assertEqual(fresh.inventory(remounted,'123','games',scan),
                         [{'id':'game','title':'Game'}])
        scan.assert_called_once()
        (remounted/'ISO/Game.iso').write_bytes(b'new game content')
        fresh.inventory(remounted,'123','games',scan)
        self.assertEqual(scan.call_count,2, 'A changed file still invalidates the cache')

    def test_previous_path_scoped_cache_is_migrated_without_rescanning(self):
        scan=Mock(return_value=[])
        self.memory.inventory(self.root,'123','games',scan)
        old_name=_hash(['123',str(self.root.absolute()),'games'])+'.json'
        cache=next((self.base/'pc/catalog-cache').glob('*.json'))
        legacy=cache.with_name(old_name)
        cache.rename(legacy)
        self.assertEqual(DeviceMemory(self.base/'pc',atomic_json).inventory(
            self.root,'123','games',scan),[])
        self.assertTrue(cache.is_file(), 'The verified old cache is migrated')
        scan.assert_called_once()

    def test_emulator_rom_and_eboot_changes_invalidate_catalog(self):
        scan=Mock(return_value={'emulators':[],'roms':[]})
        self.memory.inventory(self.root,'123','retro',scan)
        rom=self.root/'PSP/GAME/gpSP/ROMs/test.gba'
        rom.parent.mkdir(parents=True)
        rom.write_bytes(b'rom')
        self.memory.inventory(self.root,'123','retro',scan)
        rom.write_bytes(b'changed rom')
        self.memory.inventory(self.root,'123','retro',scan)
        rom.unlink()
        self.memory.inventory(self.root,'123','retro',scan)
        (rom.parent.parent/'EBOOT.PBP').write_bytes(b'eboot')
        self.memory.inventory(self.root,'123','retro',scan)
        self.assertEqual(scan.call_count,5)


if __name__=='__main__':unittest.main()
