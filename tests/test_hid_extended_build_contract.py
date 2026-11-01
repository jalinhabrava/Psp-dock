"""Experimental long HID build must preserve the proven 90s lab untouched."""
from pathlib import Path
import unittest


ROOT=Path(__file__).resolve().parents[1]/'homebrew'


class ExtendedHidBuildContractTests(unittest.TestCase):
    def test_proven_lab_keeps_original_watchdog(self):
        kernel=(ROOT/'hid-only-lab/lab_core.c').read_text(encoding='utf-8')
        user=(ROOT/'hid-only-lab/main.c').read_text(encoding='utf-8')
        self.assertIn('#define TIMEOUT_US 90000000u',kernel)
        self.assertIn('elapsed < 1050',user)
        self.assertIn('PSPDockHidOnly/bridgehid.prx',user)

    def test_extended_build_is_separate_and_has_gate_checks(self):
        build=(ROOT/'hid-extended/build-wsl.sh').read_text(encoding='utf-8')
        self.assertIn('hid-extended-15m',build)
        self.assertIn('900000000u',build)
        self.assertIn('elapsed < 9200',build)
        self.assertIn('PSPDockHidLong',build)
        self.assertIn('Unexpected USBSTOR import',build)
        self.assertIn('Unsafe direct USB/kernel import',build)
        self.assertNotIn('ms0:/',build)
        self.assertNotIn('flash0:',build)


if __name__=='__main__':
    unittest.main()
