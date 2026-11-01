from dataclasses import asdict
import unittest

import hid_input_probe as probe


PATH = r"\\?\hid#vid_054c&pid_01cb#test#{4d1e55b2-f16f-11cf-88cb-001111000030}"
SESSION = "ab" * 16


def report(axes=(127, 127, 127, 127), hat=0, buttons=0,
           padding=(0, 0)):
    return bytes((0, *axes,
                  (hat & 0x0F) | ((buttons & 0x0F) << 4),
                  (buttons >> 4) & 0xFF, *padding))


class HidInputProbeTests(unittest.TestCase):
    def test_neutral_axes_and_all_buttons_up(self):
        result = probe.decode_input_report(report())
        self.assertEqual(result.axes, (127, 127, 127, 127))
        self.assertEqual(result.hat, 0)
        self.assertEqual(result.buttons_mask, 0)
        self.assertEqual(result.pressed, ())
        self.assertTrue(result.reserved_zero)

    def test_all_twelve_button_positions(self):
        for i, expected in enumerate(probe.BUTTON_NAMES):
            with self.subTest(button=expected):
                pressed = probe.decode_input_report(report(buttons=(1 << i)))
                self.assertEqual(pressed.pressed, (expected,))
                self.assertEqual(pressed.buttons_mask, 1 << i)
        all_pressed = probe.decode_input_report(report(buttons=0xFFF))
        self.assertEqual(all_pressed.pressed, probe.BUTTON_NAMES)
        self.assertEqual(all_pressed.buttons_mask, 4095)

    def test_analog_and_hat_direction(self):
        result = probe.decode_input_report(
            report(axes=(1, 254, 127, 220), hat=8, buttons=17))
        self.assertEqual(result.axes, (1, 254, 127, 220))
        self.assertEqual(result.hat, 8)
        self.assertEqual(result.pressed, ("CROSS", "L"))

    def test_malformed_input_report_rejected(self):
        for bad in (
            b"", bytes(8), bytes(10), bytes((1, *report()[1:])),
            report(hat=9), report(padding=(1, 0)),
            report(padding=(0, 1)),
        ):
            with self.subTest(bad=bad), self.assertRaises(probe.InputProbeError):
                probe.decode_input_report(bad)

    def test_only_one_valid_pdk1_device(self):
        valid = lambda _: {"connected": True, "session": SESSION}
        self.assertEqual(
            probe.validate_unique_device([PATH], valid), (PATH, SESSION))
        for paths in (
            [], [PATH, PATH],
            [r"\\?\hid#vid_1234&pid_01cb#wrong"],
            [PATH, PATH.replace("01cb", "01cb&mi_01")],
        ):
            with self.subTest(paths=paths), self.assertRaises(probe.InputProbeError):
                probe.validate_unique_device(paths, valid)
        for status in (
            {"connected": False, "session": SESSION},
            {"connected": True, "session": ""},
        ):
            with self.subTest(status=status), self.assertRaises(probe.InputProbeError):
                probe.validate_unique_device([PATH], lambda _: status)


if __name__ == "__main__":
    unittest.main()
