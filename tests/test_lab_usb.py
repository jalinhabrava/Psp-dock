import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools.labs.lab_return as lab_return
from psp_dock.devices.psp_usb import PspStorage, UsbTransitionError


class LabReturnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        self.now = datetime(2026, 10, 31, 21, 0, 0)
        self.storage = {"volume_serial": "12345678", "usb_device_id": r"USB\VID_054C&PID_02D2\PSP"}

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, minutes=1, accepted=True):
        event = {
            "experiment": "host_safe_eject_psp_probe",
            "time": (self.now - timedelta(minutes=minutes)).isoformat(),
            "host": {"host_eject_accepted": accepted},
            "storage": self.storage,
        }
        (self.root / "docs" / "probe-eject-20261031-205900.json").write_text(json.dumps(event))

    def test_requires_successful_recent_host_release(self):
        with patch.object(lab_return, "ROOT", self.root):
            with self.assertRaises(UsbTransitionError):
                lab_return.last_accepted_eject(self.now)
            self.write(accepted=False)
            with self.assertRaises(UsbTransitionError):
                lab_return.last_accepted_eject(self.now)
            self.write(minutes=120)
            with self.assertRaisesRegex(UsbTransitionError, "antigua"):
                lab_return.last_accepted_eject(self.now)
            self.write()
            self.assertEqual(lab_return.last_accepted_eject(self.now), self.storage)

    def test_requires_same_volume_and_usb_parent(self):
        orig = PspStorage("G:\\", "12345678", "sony-disk", self.storage["usb_device_id"], "SONY PSP")
        wrong = PspStorage("H:\\", "12345678", "sony-disk", self.storage["usb_device_id"] + "WRONG", "SONY PSP")
        with patch.object(lab_return, "windows_devices", return_value=[{"root": "G:\\"}, {"root": "H:\\"}]), \
             patch.object(lab_return, "identify", side_effect=[(orig, 3), (wrong, 4)]):
            self.assertEqual(lab_return.matching_storage(self.storage), ["G:\\"])


if __name__ == "__main__":
    unittest.main()
