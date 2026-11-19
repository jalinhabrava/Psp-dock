"""SDL launch tests use synthetic sysfs, never a real joystick."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

from psp_dock.emulation import linux_sdl as sdl

GUID = "030000004c050000cd01000001030000"


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class MappingTests(unittest.TestCase):
    def test_sdl_layout_maps_physical_psp_buttons_and_hat(self):
        mapping = sdl.controller_mapping(GUID)
        for field in ("a:b0", "b:b1", "x:b2", "y:b3", "leftshoulder:b4",
                      "rightshoulder:b5", "back:b6", "start:b7", "guide:b8",
                      "dpup:h0.1", "dpright:h0.2", "dpdown:h0.4", "dpleft:h0.8"):
            self.assertIn(field + ",", mapping)
        self.assertIn("leftx:a0,lefty:a1,", mapping)
        self.assertNotIn("righttrigger", mapping)

    def test_sdl_profile_does_not_use_windows_button_codes(self):
        text = sdl.ppsspp_controls(2).decode()
        for key, value in {"Cross":189, "Circle":190, "Square":191, "Triangle":188,
                           "L":193, "R":192, "Select":196, "Start":197,
                           "An.Left":4001, "An.Right":4000,
                           "An.Up":4003, "An.Down":4002}.items():
            self.assertIn(f"{key} = 12-{value}\n", text)
        self.assertIn("Pause = 12-4,1-111\n", text)

    def test_rejects_injected_or_unknown_guid_and_invalid_index(self):
        for value in ("0"*32, GUID+",a:b9", GUID+"\n", "../event0", None):
            with self.subTest(value=value), self.assertRaises(sdl.LinuxControllerError):
                sdl.controller_mapping(value)
        for value in (-1, 10, True, "0"):
            with self.subTest(value=value), self.assertRaises(sdl.LinuxControllerError):
                sdl.ppsspp_controls(value)

    def test_test_guard_prevents_loading_real_sdl(self):
        with patch.dict(os.environ, {"PSP_DOCK_TEST_NO_REAL_USB":"1"}), \
                patch.object(sdl.C, "CDLL") as load:
            with self.assertRaises(sdl.LinuxControllerError):
                sdl.enumerate_psp_pads()
            load.assert_not_called()


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class PhysicalBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sys = Path(self.temp.name)/"sys"
        self.parent = self.make_usb("1-2")
        node = self.parent/"1-2:1.1"/"hid"/"input"/"input12"
        node.mkdir(parents=True)
        entry = self.sys/"class/input/event7"
        entry.mkdir(parents=True)
        try:
            (entry/"device").symlink_to(node, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Creating a synthetic sysfs needs directory symlinks")
        self.pad = sdl.SDLPad(0, GUID, "/dev/input/event7",
                             sdl.VID, sdl.PID, 0x0100, 4, 12, 1)

    def make_usb(self, name):
        parent = self.sys/"devices/usb1"/name
        parent.mkdir(parents=True)
        for key, value in {"idVendor":"054c", "idProduct":"01cd", "bcdDevice":"0100"}.items():
            (parent/key).write_text(value+"\n", encoding="ascii")
        return parent

    def choose(self, pads=None, parent=None):
        return sdl.select_psp_pad([self.pad] if pads is None else pads,
                                 self.parent if parent is None else parent,
                                 sysfs_root=self.sys)

    def test_matches_actual_usb_parent_and_not_just_vid_pid(self):
        self.assertEqual(self.choose(), self.pad)
        other = self.make_usb("1-3")
        with self.assertRaises(sdl.LinuxControllerError):
            self.choose(parent=other)

    def test_ambiguous_same_device_is_rejected(self):
        with self.assertRaises(sdl.LinuxControllerError):
            self.choose([self.pad, replace(self.pad,index=1)])

    def test_stale_revision_and_malformed_descriptor_are_rejected(self):
        for value in (replace(self.pad,revision=0x0300), replace(self.pad,revision=0x0301), replace(self.pad,axes=2),
                      replace(self.pad,buttons=8), replace(self.pad,hats=0),
                      replace(self.pad,index=10), replace(self.pad,index=True),
                      replace(self.pad,guid="0"*32)):
            with self.subTest(value=value), self.assertRaises(sdl.LinuxControllerError):
                self.choose([value])

    def test_sysfs_revision_is_rechecked_independently_of_sdl(self):
        (self.parent/"bcdDevice").write_text("0300\n")
        with self.assertRaises(sdl.LinuxControllerError):
            self.choose()

    def test_usb_revision_0308_also_requires_matching_kernel_and_sdl_metadata(self):
        (self.parent/"bcdDevice").write_text("0308\n")
        with self.assertRaises(sdl.LinuxControllerError):
            self.choose()
        pad = replace(self.pad, revision=0x0308)
        self.assertEqual(self.choose([pad]), pad)

    def test_unlinked_input_and_path_traversal_are_rejected(self):
        for name in ("/dev/input/event999", "/dev/input/../event7", "event7", "/tmp/event7"):
            with self.subTest(name=name), self.assertRaises(sdl.LinuxControllerError):
                self.choose([replace(self.pad,path=name)])

    def test_parent_outside_sysfs_devices_is_rejected(self):
        other = self.sys/"arbitrary"
        other.mkdir()
        with self.assertRaises(sdl.LinuxControllerError):
            self.choose(parent=other)

    def test_child_environment_is_private_and_unrelated_mappings_survive(self):
        old = { "HOME": "/home/player", "XDG_CONFIG_HOME": "/home/player/.config",
                "SDL_GAMECONTROLLERCONFIG": GUID+",old,a:b10,\n"+
                    "other-controller,Keep Me,a:b4," }
        before = dict(old)
        env, controls, pad = sdl.launch_profile(
            self.parent, environ=old, pads=[self.pad], sysfs_root=self.sys)
        self.assertEqual(old, before)
        self.assertEqual(env["HOME"], old["HOME"])
        self.assertEqual(env["XDG_CONFIG_HOME"], old["XDG_CONFIG_HOME"])
        self.assertIn("other-controller,Keep Me,a:b4,", env["SDL_GAMECONTROLLERCONFIG"])
        self.assertNotIn(",old,", env["SDL_GAMECONTROLLERCONFIG"])
        self.assertEqual(env["SDL_GAMECONTROLLERCONFIG"].count(GUID), 1)
        self.assertEqual(controls, sdl.ppsspp_controls(0))
        self.assertEqual(pad, self.pad)

    def test_final_launch_check_rejects_changed_index_guid_or_missing_pad(self):
        self.assertEqual(sdl.verify_psp_pad(self.pad, self.parent, pads=[self.pad], sysfs_root=self.sys), self.pad)
        for pads in ([], [replace(self.pad, index=1)], [replace(self.pad, guid='ab'*16)]):
            with self.subTest(pads=pads), self.assertRaises(sdl.LinuxControllerError):
                sdl.verify_psp_pad(self.pad, self.parent, pads=pads, sysfs_root=self.sys)
        with self.assertRaises(sdl.LinuxControllerError):
            sdl.verify_psp_pad(None, self.parent, pads=[self.pad], sysfs_root=self.sys)


if __name__ == "__main__":
    unittest.main()
