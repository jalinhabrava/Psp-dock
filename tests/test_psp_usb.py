import unittest
from unittest.mock import patch

from psp_dock.devices.psp_usb import PspStorage, UsbTransitionError, validate_identity, safe_eject

DISK = r'USBSTOR\DISK&VEN_SONY&PROD_"PSP"_MS&REV_1.00\8A67FB08B1B4B5A6&0'
USB = r'USB\VID_054C&PID_02D2\8A67FB08B1B4B5A6'


class PSPUSBTests(unittest.TestCase):
    def test_accepts_bound_psp_storage(self):
        p = validate_identity('g:\\', '31930445', 'SONY "PSP" MS USB Device', DISK, USB)
        self.assertEqual(p.drive, 'G:\\')

    def test_never_confuses_generic_disk_with_psp(self):
        valid = ('G:\\', '31930445', 'SONY "PSP" MS USB Device', DISK, USB)
        for index, replacement in ((0, 'G:\\PSP'), (1, ''),
                                   (1, 'abc'), (2, 'Other USB Disk'),
                                   (3, DISK.replace('SONY', 'OTHER')),
                                   (4, USB.replace('054C', 'AAAA'))):
            args = list(valid)
            args[index] = replacement
            with self.subTest(index=index), self.assertRaises(UsbTransitionError):
                validate_identity(*args)

    def test_mismatched_volume_never_calls_device_eject(self):
        p = PspStorage('G:\\', '31930445', DISK, USB, 'SONY "PSP" MS USB Device')
        with patch('psp_dock.devices.psp_usb.identify', return_value=(p, 42)) as identity:
            with self.assertRaisesRegex(UsbTransitionError, 'cambió'):
                safe_eject('G:\\', 'bad-volume')
        identity.assert_called_once()


if __name__ == '__main__':
    unittest.main()
