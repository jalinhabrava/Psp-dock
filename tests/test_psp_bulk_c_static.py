"""Static checks that the experimental PSP descriptor code never executes."""
from pathlib import Path
import unittest


class USBDescriptorPrototypeSourceTests(unittest.TestCase):
    def test_only_static_pspdev_descriptors(self):
        base=Path(__file__).resolve().parents[1]/'homebrew/psp-bulk-prototype'
        code=(base/'composite_layout.c').read_text(encoding='utf-8')
        self.assertIn('.bNumInterfaces = 2',code)
        self.assertIn('.bInterfaceClass = 0xff',code)
        self.assertIn('BULK_IN(512)',code)
        self.assertIn('BULK_OUT(512)',code)
        self.assertIn('BULK_IN(64)',code)
        self.assertIn('BULK_OUT(64)',code)
        for forbidden in ('sceUsbStart(','sceUsbbdRegister(','flash0:/','ms0:/','module_start('):
            self.assertNotIn(forbidden,code)

    def test_pdb2_c_has_no_usb_or_psp_fat_calls(self):
        base=Path(__file__).resolve().parents[1]/'homebrew/psp-bulk-prototype'
        code=(base/'pdb2_wire.c').read_text(encoding='utf-8')
        for forbidden in ('sceUsb', 'sceIo', 'sceUsbbd', 'flash0:', 'ms0:'):
            self.assertNotIn(forbidden,code)
        self.assertIn('pdb2_decode',code)
        self.assertIn('pdb2_crc32',code)
