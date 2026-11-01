import unittest
from unittest.mock import patch

import psp_winusb_probe as w


class BulkWinUsbPrototypeTests(unittest.TestCase):
    def test_immutable_interface_identity_and_real_guid_format(self):
        self.assertEqual(w.INTERFACE_NUMBER,1)
        self.assertEqual(w.VID,0x054c)
        self.assertEqual(w.PID,0x01cb)
        self.assertEqual(len(str(w.INTERFACE_GUID)),36)
        path=(r'\\?\usb#vid_054c&pid_01cb&mi_01#'
              r'7&12345678&0&0001#{12345678-1234-5678-9012-123456789abc}')
        self.assertTrue(w.expected_path(path))
        self.assertFalse(w.expected_path(path.replace('mi_01','mi_00')))
        self.assertFalse(w.expected_path(path.replace('pid_01cb','pid_02d2')))
        self.assertFalse(w.expected_path(r'\\?\hid#vid_054c&pid_01cb#x'))

    def test_vendor_bulk_contract_fails_closed(self):
        good=[(0x82,2,512),(0x03,2,512)]
        checked=w.parse_interface_info(vendor_id=0x054c,product_id=0x01cb,
                                       interface=1,class_code=0xff,endpoints=good)
        self.assertTrue(checked['high_speed'])
        self.assertFalse(checked['bulk_transport_ready'])
        for invalid in (
            [(0x81,2,512),(0x03,2,512)],
            [(0x82,3,512),(0x03,2,512)],
            [(0x82,2,512),(0x03,2,64)],
            [(0x82,2,32),(0x03,2,32)],
        ):
            with self.subTest(value=invalid),self.assertRaises(w.WinUsbPrototypeError):
                w.parse_interface_info(vendor_id=0x054c,product_id=0x01cb,
                                        interface=1,class_code=0xff,endpoints=invalid)
        with self.assertRaises(w.WinUsbPrototypeError):
            w.parse_interface_info(vendor_id=0x054c,product_id=0x02d2,
                                    interface=1,class_code=0xff,endpoints=good)

    def test_probe_does_not_modify_usb_or_claim_hardware_success(self):
        with patch.object(w.os,'name','nt'),\
             patch.object(w,'candidate_paths',return_value=[]):
            result=w.probe()
        self.assertTrue(result['supported'])
        self.assertEqual(result['candidate_paths'],[])
        self.assertFalse(result['bulk_ready'])
        with patch.object(w.os,'name','posix'):
            self.assertFalse(w.probe()['supported'])


if __name__=='__main__':
    unittest.main()
