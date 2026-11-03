import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from device_memory import DeviceMemory
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


if __name__=='__main__':unittest.main()
