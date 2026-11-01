"""USB descriptors are audited as DATA: no PSP device is touched."""
import struct
import unittest

from psp_usb_composite import (
    DescriptorError,descriptor_set,audit_configuration,
    CONFIG_SIZE,DEVICE_SIZE,
)


class CompositePrototypeTests(unittest.TestCase):
    def test_both_speeds_have_gamepad_and_vendor_bulk_in_one_config(self):
        for high_speed,packet in ((True,512),(False,64)):
            with self.subTest(high_speed=high_speed):
                result=descriptor_set(high_speed=high_speed,report_length=86)
                self.assertEqual(len(result.device),DEVICE_SIZE)
                self.assertEqual(len(result.config),CONFIG_SIZE)
                self.assertEqual(result.device[4],0)
                self.assertFalse(result.attested_hardware)
                self.assertFalse(result.winusb_bound)
                audit=audit_configuration(result.config)
                self.assertEqual(audit['interfaces'],2)
                self.assertEqual(audit['bulk_packet_bytes'],packet)
                self.assertEqual(audit['hid_report_length'],86)
                self.assertFalse(audit['physical_psp_verified'])

    def test_malformed_descriptors_fail_closed(self):
        valid=descriptor_set(high_speed=True,report_length=40)
        for corrupt in (
            valid.config[:-2],
            bytes((0,))+valid.config[1:],
            valid.config[:4]+b'\x01'+valid.config[5:],
            valid.config.replace(b'\x82\x02\x00\x02',b'\x81\x02\x00\x02'),
            valid.config.replace(b'\x03\x02\x00\x02',b'\x04\x03\x00\x02'),
        ):
            with self.subTest(corrupt=corrupt[-8:]),self.assertRaises(DescriptorError):
                audit_configuration(corrupt)
        with self.assertRaises(DescriptorError):
            descriptor_set(high_speed=True,report_length=0)
        with self.assertRaises(DescriptorError):
            descriptor_set(high_speed=1,report_length=80)


if __name__=="__main__":
    unittest.main()
