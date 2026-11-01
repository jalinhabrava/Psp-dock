import unittest
import ctypes as C

import hid_winmm_buttons as joy


class HidWinmmButtonTests(unittest.TestCase):
    def test_windows_joy_structures(self):
        self.assertEqual(C.sizeof(joy.JOYINFOEX), 52)
        self.assertEqual(len(joy._CAPS_UINTS), 19)

    def test_requires_unique_expected_vid_pid(self):
        wanted = {"index": 3, "vendor_id": 0x054C, "product_id": 0x01CB}
        self.assertEqual(joy.matching_joystick_capabilities([
            {"index": 0, "vendor_id": 0x1234, "product_id": 0x01CB}, wanted
        ]), wanted)
        for rows in ([], [wanted, wanted],
                     [{"index": 2, "vendor_id": 0x054C, "product_id": 0x02D2}]):
            with self.assertRaises(joy.JoyProbeError):
                joy.matching_joystick_capabilities(rows)

    def test_msc_and_stable_gamepads_are_selected_independently(self):
        stable = {'index': 0, 'vendor_id': 0x054C, 'product_id': 0x01CB}
        msc = {'index': 1, 'vendor_id': 0x054C, 'product_id': 0x01CC}
        self.assertEqual(joy.matching_joystick_capabilities([stable, msc]), stable)
        self.assertEqual(joy.matching_joystick_capabilities([stable, msc], expected_product=0x01CC), msc)
        with self.assertRaises(joy.JoyProbeError):
            joy.matching_joystick_capabilities([stable], expected_product=0x01CC)
        with self.assertRaises(joy.JoyProbeError):
            joy.matching_joystick_capabilities([msc])

    def test_buttons_and_pov_are_decoded_without_inference(self):
        idle = joy.decode_joyinfo(x=32767, y=32767, z=32767, r=32767,
                                  buttons=0, pov=0xFFFF)
        self.assertEqual(idle.pressed, ())
        self.assertEqual(idle.button_mask, 0)
        pressed = joy.decode_joyinfo(x=32767, y=32767, z=0, r=65535,
                                     buttons=(1 << 0) | (1 << 11), pov=9000)
        self.assertEqual(pressed.pressed, (1, 12))
        self.assertEqual(pressed.axes, (32767, 32767, 0, 65535))

    def test_invalid_values_rejected(self):
        for buttons in (-1, 1 << 32, "0"):
            with self.assertRaises(joy.JoyProbeError):
                joy.decode_joyinfo(x=0, y=0, z=0, r=0, buttons=buttons, pov=0)


if __name__ == "__main__":
    unittest.main()
