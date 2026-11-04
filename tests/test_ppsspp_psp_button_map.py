from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import psp_dock.emulation.ppsspp_psp_button_map as mapping


DEFAULT_PROFILE = (
    "[ControlMapping]\n"
    "Up = 1-19,20-19,10-19\n"
    "Down = 1-20,20-20,10-20\n"
    "Left = 1-21,20-21,10-21\n"
    "Right = 1-22,20-22,10-22\n"
    "Circle = 1-52,20-97,10-190\n"
    "Cross = 1-54,20-96,10-189\n"
    "Square = 1-29,20-99,10-191\n"
    "Triangle = 1-47,20-100,10-188\n"
    "Start = 1-62,20-108,10-197\n"
    "Select = 1-50,20-109,10-196\n"
    "L = 1-45,20-102,10-194\n"
    "R = 1-51,20-103,10-195\n"
    "An.Up = 1-37,20-4002,10-4003\n"
    "An.Down = 1-39,20-4003,10-4002\n"
    "An.Left = 1-38,20-4001,10-4001\n"
    "An.Right = 1-40,20-4000,10-4000\n"
    "Pause = 1-111,20-4034,20-3,20-4046,10-109,10-104,10-4034\n"
    "Rewind = 1-67\n"
).encode()


class PspButtonMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ini = self.root / "memstick/PSP/SYSTEM/controls.ini"
        self.ini.parent.mkdir(parents=True)
        self.ini.write_bytes(DEFAULT_PROFILE)
        self.backups = self.root / "profile-backups"

    def tearDown(self):
        self.temp.cleanup()

    def test_physical_psp_button_order_matches_final_native_driver(self):
        hid_code = (
            Path(__file__).resolve().parents[1]
            / "homebrew/native-dock/native_driver.c"
        ).read_text(encoding="utf-8")
        start = hid_code.index("static const uint32_t masks[12]")
        order = hid_code[start:hid_code.index("};", start)]
        physical = (
            "PSP_CTRL_CROSS", "PSP_CTRL_CIRCLE", "PSP_CTRL_SQUARE",
            "PSP_CTRL_TRIANGLE", "PSP_CTRL_LTRIGGER", "PSP_CTRL_RTRIGGER",
            "PSP_CTRL_SELECT", "PSP_CTRL_START",
        )
        positions = [order.index(name) for name in physical]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(list(mapping.PHYSICAL_BUTTON_TO_KEY.values()),
                         list(range(188, 196)))

    def test_remaps_exact_psp_controls_without_changing_other_inputs(self):
        updated, changed = mapping.remap_controls(DEFAULT_PROFILE)
        self.assertTrue(changed)
        text = updated.decode()
        expected = {
            "Cross": "1-54,20-96,10-188",
            "Circle": "1-52,20-97,10-189",
            "Square": "1-29,20-99,10-190",
            "Triangle": "1-47,20-100,10-191",
            "L": "1-45,20-102,10-192",
            "R": "1-51,20-103,10-193",
            "Select": "1-50,20-109,10-194",
            "Start": "1-62,20-108,10-195",
        }
        for key, value in expected.items():
            with self.subTest(button=key):
                self.assertIn(f"{key} = {value}\n", text)
        for original in (
            "Up = 1-19,20-19,10-19",
            "Down = 1-20,20-20,10-20",
            "An.Up = 1-37,20-4002,10-4003",
            "An.Right = 1-40,20-4000,10-4000",
            "Pause = 1-111,20-4034,20-3,20-4046,10-109,10-104,10-4034",
            "Rewind = 1-67",
        ):
            self.assertIn(original, text)
        same, changed_again = mapping.remap_controls(updated)
        self.assertFalse(changed_again)
        self.assertEqual(same, updated)

    def test_preserves_crlf_bom_and_comments(self):
        original = b"\xef\xbb\xbf; PSP\r\n" + DEFAULT_PROFILE.replace(
            b"\n", b"\r\n"
        )
        changed, applied = mapping.remap_controls(original)
        self.assertTrue(applied)
        self.assertTrue(changed.startswith(b"\xef\xbb\xbf; PSP\r\n"))
        self.assertEqual(changed.count(b"\r\n"), original.count(b"\r\n"))
        self.assertEqual(changed.count(b"\n"), original.count(b"\n"))

    def test_rejects_unknown_manual_or_partial_changes(self):
        bad_profiles = [
            DEFAULT_PROFILE.replace(b"Cross = 1-54,20-96,10-189",
                                    b"Cross = 1-54,20-96,10-188"),
            DEFAULT_PROFILE.replace(b"Circle = 1-52,20-97,10-190",
                                    b"Circle = 1-52,20-97,10-219"),
            DEFAULT_PROFILE + b"Cross = 10-188\n",
            DEFAULT_PROFILE.replace(b"Circle = 1-52,20-97,10-190",
                                    b"Circle = 1-52,20-97,10-190,10-189"),
            DEFAULT_PROFILE.replace(b"[ControlMapping]\n",
                                    b"[Unknown]\n"),
            DEFAULT_PROFILE + b"[ControlMapping]\n",
        ]
        for profile in bad_profiles:
            with self.subTest(profile=profile[:60]):
                with self.assertRaises(mapping.MappingError):
                    mapping.remap_controls(profile)

    def test_atomic_backup_only_in_private_pc_path(self):
        with patch.object(mapping, "ppsspp_open", return_value=False):
            preview = mapping.apply_profile(self.ini, self.backups)
            self.assertEqual(preview["status"], "PREVIEW")
            self.assertEqual(self.ini.read_bytes(), DEFAULT_PROFILE)
            self.assertFalse(self.backups.exists())
            result = mapping.apply_profile(self.ini, self.backups, approve=True)
            self.assertTrue(result["changed"])
            self.assertEqual(Path(result["backup"]).read_bytes(), DEFAULT_PROFILE)
            self.assertEqual(result["sha256_before"],
                             hashlib.sha256(DEFAULT_PROFILE).hexdigest())
            second = mapping.apply_profile(self.ini, self.backups, approve=True)
            self.assertFalse(second["changed"])
            self.assertEqual(list(self.backups.glob("*.ini")),
                             [Path(result["backup"])])

    def test_ppsspp_open_blocks_any_edit(self):
        with patch.object(mapping, "ppsspp_open", return_value=True):
            with self.assertRaisesRegex(mapping.MappingError, "Cierra PPSSPP"):
                mapping.apply_profile(self.ini, self.backups, approve=True)
        self.assertEqual(self.ini.read_bytes(), DEFAULT_PROFILE)
        self.assertFalse(self.backups.exists())

    def test_failed_publish_preserves_original_and_backup(self):
        with patch.object(mapping, "ppsspp_open", return_value=False), \
                patch.object(mapping.os, "replace", side_effect=OSError("simulated failure")):
            with self.assertRaisesRegex(OSError, "simulated failure"):
                mapping.apply_profile(self.ini, self.backups, approve=True)
        self.assertEqual(self.ini.read_bytes(), DEFAULT_PROFILE)
        self.assertEqual(next(self.backups.glob("*.ini")).read_bytes(),
                         DEFAULT_PROFILE)


if __name__ == "__main__":
    unittest.main()
