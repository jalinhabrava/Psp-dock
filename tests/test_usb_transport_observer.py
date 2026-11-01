import unittest
from unittest.mock import patch
from pathlib import Path

import usb_transport_observer as obs
from psp_usb import PspStorage, UsbTransitionError


STORAGE = {
    "drive": "G:\\", "volume_serial": "2958455453",
    "usb_device_id": "USB\\VID_054C&PID_02D2\\8A67FB08",
}
SONY_ID = STORAGE["usb_device_id"]


class TransportObserverTests(unittest.TestCase):
    def test_all_states_fail_closed_without_live_fat_ack(self):
        cases = (
            (STORAGE, [], [], "STORAGE_VISIBLE"),
            (STORAGE, [{"session": "valid"}], [], "DUAL_PRESENT_UNSAFE"),
            (None, [{"session": "valid"}], [], "HID_VISIBLE_UNBOUND"),
            (None, [], [{"pnp_id": SONY_ID, "problem_code": 47}],
             "WINDOWS_PREPARED_REMOVAL"),
            (None, [], [], "NO_PSP_INTERFACE_OBSERVED"),
        )
        for storage, hid, pnp, expected in cases:
            with self.subTest(expected=expected):
                result = obs.classify_observation(
                    storage=storage, hid_devices=hid, pnp_nodes=pnp)
                self.assertEqual(result.state, expected)
                self.assertFalse(result.role_transition_confirmed)
                self.assertFalse(result.exclusive_fat_confirmed)
                self.assertFalse(result.live_firmware_ack)
                self.assertTrue(result.warnings)

    def test_pnp_selects_only_relevant_sony_devices(self):
        selected = obs.select_sony_pnp_nodes([
            {"PNPDeviceID": SONY_ID, "ConfigManagerErrorCode": 0,
             "Name": "PSP", "Status": "OK"},
            {"PNPDeviceID": 'USBSTOR\\DISK&VEN_SONY&PROD_"PSP"_MS&REV_1.00\\INSTANCE',
             "ConfigManagerErrorCode": 47, "Name": "PSP drive", "Status": "Error"},
            {"PNPDeviceID": "USB\\VID_054C&PID_05C4\\CONTROLLER", "ConfigManagerErrorCode": 0},
            {"PNPDeviceID": "USB\\VID_1234&PID_02D2\\WRONG", "ConfigManagerErrorCode": 0},
            {"PNPDeviceID": "USB\\VID_054C&PID_02D2EVIL\\WRONG",
             "ConfigManagerErrorCode": 0},
        ])
        self.assertEqual(len(selected), 2)
        self.assertEqual({x["problem_code"] for x in selected}, {0, 47})

    def test_problem_47_warning_even_when_volume_still_appears(self):
        report = obs.classify_observation(
            storage=STORAGE, hid_devices=[],
            pnp_nodes=[{"pnp_id": SONY_ID, "problem_code": 47}],
        )
        self.assertEqual(report.state, "STORAGE_VISIBLE")
        self.assertTrue(any("47" in warning for warning in report.warnings))
        self.assertFalse(report.live_firmware_ack)

    @patch.object(obs, "windows_pnp_snapshot", return_value=[])
    @patch.object(obs, "hid_probe", return_value={"supported": True, "devices": [], "errors": []})
    @patch.object(obs, "identify", return_value=(PspStorage(
        "G:\\", "2958455453", "USBSTOR\\DISK&VEN_SONY&PROD_PSP",
        SONY_ID, 'SONY "PSP" MS USB Device'), 15))
    @patch.object(obs, "windows_devices", return_value=[{"root": "G:\\"}])
    def test_live_read_only_collection(self, devices, identify, hid, pnp):
        value = obs.sample_once()
        self.assertEqual(value.state, "STORAGE_VISIBLE")
        self.assertEqual(value.volume_serial, "2958455453")
        self.assertFalse(value.exclusive_fat_confirmed)
        self.assertFalse(value.live_firmware_ack)
        identify.assert_called_once_with("G:\\")
        hid.assert_called_once_with()
        pnp.assert_called_once_with()

    @patch.object(obs, "windows_devices", return_value=[{"root": "G:\\"}, {"root": "H:\\"}])
    def test_ambiguous_storage_identity_rejected(self, devices):
        with self.assertRaisesRegex(obs.ObservationError, "varias"):
            obs.sample_once()

    @patch.object(obs, "identify", side_effect=UsbTransitionError("foreign"))
    @patch.object(obs, "windows_devices", return_value=[{"root": "G:\\"}])
    def test_foreign_volume_rejected(self, devices, identify):
        with self.assertRaisesRegex(obs.ObservationError, "Sony"):
            obs.sample_once()

    def test_observer_has_no_mutating_usb_functions(self):
        source = Path(obs.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "safe_eject(", "DeviceIoControl(", "CM_Request_Device_Eject",
            "IOCTL_STORAGE_EJECT_MEDIA", "sceUsbStart(", "sceUsbStop(",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
