import unittest

from hid_lab_watch import classify


class HidLabWatchTests(unittest.TestCase):
    def test_observer_recognizes_only_valid_independent_hid(self):
        connected = [{"status": {"connected": True, "session": "01" * 16}}]
        disconnected = [{"status": {"connected": False, "session": "01" * 16}}]
        self.assertEqual(classify(0, connected, []), "HID_PDK1_VALID")
        self.assertEqual(classify(0, disconnected, []), "HID_VISIBLE_NOT_CONFIGURED")
        self.assertEqual(classify(0, [], []), "NO_PSP_INTERFACE")
        self.assertEqual(classify(1, [], []), "STORAGE_VISIBLE")
        self.assertEqual(classify(1, connected, []), "STORAGE_AND_HID_UNSAFE")
        self.assertEqual(classify(0, connected, ["bad path"]), "HID_PROBE_AMBIGUOUS")
        self.assertEqual(classify(0, connected * 2, []), "MULTIPLE_DEVICES_UNSAFE")


if __name__ == "__main__":
    unittest.main()
