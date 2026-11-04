import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.labs import lab_return
from psp_dock.devices.psp_usb import PspStorage, UsbTransitionError


class LabReturnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        self.clock = datetime(2026, 10, 31, 21, 0, 0)
        self.card = {"volume_serial": "12345678",
                     "usb_device_id": r"USB\VID_054C&PID_02D2\PSP"}

    def tearDown(self):
        self.tmp.cleanup()

    def write_eject(self, minutes_ago=1, accepted=True):
        data = {
            "experiment": "host_safe_eject_psp_probe",
            "time": (self.clock - timedelta(minutes=minutes_ago)).isoformat(),
            "host": {"host_eject_accepted": accepted},
            "storage": self.card,
        }
        (self.root / "docs" / "probe-eject-20261031-205900.json").write_text(json.dumps(data))

    def test_requires_recent_successful_eject(self):
        with patch.object(lab_return, "ROOT", self.root):
            with self.assertRaises(UsbTransitionError):
                lab_return.last_accepted_eject(self.clock)
            self.write_eject(accepted=False)
            with self.assertRaises(UsbTransitionError):
                lab_return.last_accepted_eject(self.clock)
            self.write_eject(minutes_ago=120)
            with self.assertRaisesRegex(UsbTransitionError, "antigua"):
                lab_return.last_accepted_eject(self.clock)
            self.write_eject()
            self.assertEqual(lab_return.last_accepted_eject(self.clock), self.card)

    def test_matches_exact_parent_and_volume(self):
        actual = PspStorage(
            "G:\\", "12345678", "USBSTOR\\DISK&VEN_SONY&PROD_PSP",
            self.card["usb_device_id"], 'SONY "PSP" MS USB Device')
        another = PspStorage("H:\\", "12345678", actual.disk_id,
                             actual.usb_device_id + "WRONG", actual.model)
        with patch.object(lab_return, "windows_devices",
                          return_value=[{"root": "G:\\"}, {"root": "H:\\"}]), \
             patch.object(lab_return, "identify",
                          side_effect=[(actual, 3), (another, 4)]):
            self.assertEqual(lab_return.matching_storage(self.card), ["G:\\"])


if __name__ == "__main__":
    unittest.main()
