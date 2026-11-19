import unittest
from unittest.mock import Mock, patch
import psp_dock.devices.psp_launcher_input as controller
from psp_dock.devices.hid_winmm_buttons import JoyProbeError


class WindowsLauncherControllerTests(unittest.TestCase):
    def test_physical_button_map_never_automatically_launches(self):
        expected = {
            0: "accept", 1: "back", 2: "saves", 3: "graphics",
            4: "pageLeft", 5: "pageRight", 6: "exit", 7: "settings",
        }
        for bit, name in expected.items():
            with self.subTest(button=bit):
                frame = controller.normalize_psp_joystick(
                    buttons=1 << bit, pov=0xFFFF, x=32767, y=32767)
                self.assertTrue(frame[name])
                if bit != 0:
                    self.assertFalse(frame["accept"])
        self.assertTrue(controller.normalize_psp_joystick(
            buttons=0, pov=0xFFFF, x=32767, y=32767)["connected"])
        self.assertFalse(controller.normalize_psp_joystick(
            buttons=0, pov=0xFFFF, x=32767, y=32767)["accept"])

    def test_cardinals_diagonals_and_centered_axes(self):
        sample = lambda p: controller.normalize_psp_joystick(
            buttons=0, pov=p, x=32767, y=32767)
        for angle, name in ((0,"up"),(9000,"right"),(18000,"down"),(27000,"left")):
            frame = sample(angle)
            self.assertTrue(frame[name])
            self.assertEqual(sum(frame[k] for k in ("up","down","left","right")), 1)
        diagonal = sample(4500)
        self.assertTrue(diagonal["up"] and diagonal["right"])
        self.assertFalse(diagonal["left"] or diagonal["down"])
        neutral=sample(0xFFFF)
        self.assertFalse(any(neutral[k] for k in ("up","down","left","right")))

    def test_stick_deadzone_and_range_capabilities(self):
        self.assertTrue(controller.normalize_psp_joystick(
            buttons=0,pov=0xFFFF,x=10,y=125,
            x_range=(0,255),y_range=(0,255))["left"])
        self.assertTrue(controller.normalize_psp_joystick(
            buttons=0,pov=0xFFFF,x=255,y=240,
            x_range=(0,255),y_range=(0,255))["down"])
        with self.assertRaises(JoyProbeError):
            controller.normalize_psp_joystick(
                buttons=0,pov=0xFFFF,x=125,y=125,x_range=(0,0))

    def test_malformed_pov_and_extra_buttons_fail_closed(self):
        for pov in (-1,36000,"up"):
            with self.subTest(pov=pov),self.assertRaises(JoyProbeError):
                controller.normalize_psp_joystick(
                    buttons=0,pov=pov,x=32767,y=32767)
        for mask in (-1,0x1000,True):
            with self.subTest(mask=mask),self.assertRaises(JoyProbeError):
                controller.normalize_psp_joystick(
                    buttons=mask,pov=0xFFFF,x=32767,y=32767)

    def test_idle_is_safe_and_winmm_nonwindows_never_opens_device(self):
        self.assertFalse(controller.idle()["connected"])
        with patch.object(controller.os,"name","posix"):
            self.assertEqual(controller.PSPWinMMReader().read()["connected"],False)

    def test_explicit_msc_product_does_not_read_the_stable_controller(self):
        winmm = Mock()
        winmm.joyGetNumDevs.return_value = 2
        def capabilities(index, pointer, size):
            caps = controller.C.cast(pointer, controller.C.POINTER(controller.JOYCAPSW)).contents
            caps.wMid = 0x054C
            caps.wPid = 0x01CB if index == 0 else 0x01CC
            caps.wNumButtons = 8
            caps.wXmin = caps.wYmin = 0
            caps.wXmax = caps.wYmax = 65535
            return 0
        def read(index, pointer):
            info = controller.C.cast(pointer, controller.C.POINTER(controller.JOYINFOEX)).contents
            info.dwXpos = info.dwYpos = 32767
            info.dwPOV = 0xFFFF
            info.dwButtons = 1 << index
            return 0
        winmm.joyGetDevCapsW.side_effect = capabilities
        winmm.joyGetPosEx.side_effect = read
        with patch.object(controller.os, 'name', 'nt'), \
             patch.object(controller.C, 'WinDLL', return_value=winmm, create=True):
            stable = controller.PSPWinMMReader().read()
            msc = controller.PSPWinMMReader(expected_product=0x01CC).read()
        self.assertEqual(stable['button_mask'], 1)
        self.assertEqual(msc['button_mask'], 2)
        self.assertEqual([call.args[0] for call in winmm.joyGetPosEx.call_args_list], [0, 1])
        for product in (True, 0x02D2, '01CC'):
            with self.assertRaises(JoyProbeError):
                controller.PSPWinMMReader(expected_product=product)

if __name__ == "__main__":
    unittest.main()
