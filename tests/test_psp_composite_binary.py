"""Audit the compiled MIPS descriptor pointers, not a separate mock layout."""
import importlib.util
from pathlib import Path
import struct
import unittest


BASE=Path(__file__).resolve().parents[1]/'homebrew/composite-readonly'
spec=importlib.util.spec_from_file_location('pdc_elf_audit',BASE/'audit_elf.py')
audit_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)
ELF=BASE/'dist/dockcomposite.elf'


@unittest.skipUnless(ELF.is_file(), 'Run the PSPDEV build for compiled ABI verification')
class CompiledDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.raw=ELF.read_bytes()
        self.elf=audit_module.MipsElf(self.raw)

    def test_actual_compiled_mips_has_two_per_interface_settings(self):
        report=audit_module.audit(self.raw)
        self.assertTrue(report['descriptor_tables_verified'])
        self.assertEqual(report['settings_records_per_speed'],2)
        self.assertFalse(report['physical_enumeration_verified'])

    def test_old_two_pointer_layout_is_rejected(self):
        changed=bytearray(self.raw)
        address=self.elf.address('pdc_interfaces_hi')
        offset=self.elf.file_offset('pdc_settings_hi')
        struct.pack_into('<III',changed,offset,address,address+24,2)
        with self.assertRaisesRegex(audit_module.ElfAuditError,'per-interface'):
            audit_module.audit(bytes(changed))

    def test_usb_11_full_speed_would_skip_winusb_os_descriptor_query(self):
        changed=bytearray(self.raw)
        offset=self.elf.file_offset('pdc_device_full')
        struct.pack_into('<H',changed,offset+2,0x0110)
        with self.assertRaisesRegex(audit_module.ElfAuditError,'USB 2.0'):
            audit_module.audit(bytes(changed))

    def test_wrong_bulk_direction_and_pointer_do_not_pass(self):
        changed=bytearray(self.raw)
        offset=self.elf.file_offset('pdc_endpoints_hi')
        changed[offset+16+2]=0x02  # Bulk IN incorrectly turned into OUT.
        with self.assertRaises(audit_module.ElfAuditError):
            audit_module.audit(bytes(changed))
        changed=bytearray(self.raw)
        offset=self.elf.file_offset('pdc_interfaces_hi')
        struct.pack_into('<I',changed,offset+36,0)
        with self.assertRaises(audit_module.ElfAuditError):
            audit_module.audit(bytes(changed))


if __name__=='__main__':
    unittest.main()
