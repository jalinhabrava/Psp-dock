import tempfile
from pathlib import Path
import unittest

from vsh_plugin_stage import STAGE_DIR, CONFIG, ENTRY
from vsh_plugin_status import parse_boot_marker, inspect_volume


MARKER = (
    b"PSPDOCK_VSH_OBSERVER=01\n"
    b"USB=00000111\n"
    b"BUS=00000002\n"
    b"STORAGE=80243005\n"
    b"FIRMWARE_UNCHANGED=1\n"
)


class VshStatusTests(unittest.TestCase):
    def test_parse_realistic_offline_telemetry(self):
        self.assertEqual(parse_boot_marker(MARKER),
                         {"usb": 0x111, "bus": 2, "storage": 0x80243005})

    def test_bad_marker_rejected(self):
        bad = [
            MARKER.replace(b"USB=00000111", b"USB=00000311"),
            MARKER.replace(b"BUS=00000002", b"BUS=zzzzzzzz"),
            MARKER.replace(b"STORAGE=80243005", b"USB=80243005"),
            MARKER.replace(b"PSPDOCK_VSH_OBSERVER=01", b"UNKNOWN=01"),
            MARKER.replace(b"FIRMWARE_UNCHANGED=1", b"FLASH_WRITTEN=1"),
            b"\xff",
            MARKER + b"EXTRA=1\n",
        ]
        for content in bad:
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                parse_boot_marker(content)

    def test_read_only_volume_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / STAGE_DIR
            folder.mkdir(parents=True)
            (folder / "pspdockvsh.prx").write_bytes(b"fake")
            (folder / "usbstate.prx").write_bytes(b"fake")
            (folder / "BOOT.TXT").write_bytes(MARKER)
            cfg = root / CONFIG
            cfg.write_bytes(b"vsh, other.prx, on\r\n" + ENTRY.replace(b"\n", b"\r\n"))
            data_before = {p.relative_to(root): p.read_bytes()
                           for p in root.rglob("*") if p.is_file()}
            report = inspect_volume(root)
            data_after = {p.relative_to(root): p.read_bytes()
                          for p in root.rglob("*") if p.is_file()}
            self.assertEqual(data_before, data_after)
            self.assertTrue(report["disabled_entry_present"])
            self.assertFalse(report["enabled_entry_present"])
            self.assertEqual(report["plugin_config"], "OFF")
            self.assertFalse(report["armed"])
            self.assertTrue(report["boot_marker_exists"])
            self.assertEqual(report["boot_marker"]["usb"], 0x111)
            self.assertIn("puede ser antiguo", report["boot_marker_warning"])

    def test_no_status_requires_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = inspect_volume(Path(tmp))
            self.assertFalse(report["staged"])
            self.assertFalse(report["boot_marker_exists"])
            self.assertIsNone(report["boot_marker"])

    def test_enabled_plugin_is_not_misreported_as_uninstalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / STAGE_DIR
            folder.mkdir(parents=True)
            cfg = root / CONFIG
            cfg.write_bytes(ENTRY.replace(b", off", b", on"))
            state = inspect_volume(root)
            self.assertTrue(state["staged"])
            self.assertFalse(state["disabled_entry_present"])
            self.assertTrue(state["enabled_entry_present"])
            self.assertEqual(state["plugin_config"], "ON")
            self.assertFalse(state["armed"])


if __name__ == "__main__":
    unittest.main()
