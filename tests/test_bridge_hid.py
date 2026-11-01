import re
import struct
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import bridge_hid as hid

SESSION = bytes(range(1, 17))
PATH = r"\\?\hid#vid_054c&pid_01cb#test#{00000000-0000-0000-0000-000000000000}"


def status_report(**changes):
    fields = dict(magic=b"PDK1", version=1, op=1, mode=2, flags=1,
                  session=SESSION, sequence=0, result=0)
    fields.update(changes)
    return b"\0" + struct.pack("<4sBBBB16sIi", *fields.values())


class FakeHid:
    def __init__(self, status):
        self.status = status
        self.sent = []
        self.events = []

    @contextmanager
    def open(self, path, write=False):
        self.events.append(("open", path, write))
        yield 123
        self.events.append(("close",))

    def get(self, handle):
        self.events.append(("get", handle))
        return self.status

    def set(self, handle, data):
        self.events.append(("set", handle))
        self.sent.append(data)


class BridgeHidTests(unittest.TestCase):
    def test_known_wire_vector_and_windows_prefix(self):
        report = hid.encode_storage_request(SESSION.hex(), 0x12345678)
        self.assertEqual(report.hex(), "0050444b31010200000102030405060708090a0b0c0d0e0f107856341200000000")
        state = hid.decode_status(status_report())
        self.assertEqual(state.session, SESSION.hex())
        self.assertTrue(state.connected)
        self.assertFalse(state.pending)

    def test_rejects_foreign_or_malformed_status(self):
        for changes in (dict(magic=b"FuSa"), dict(version=2), dict(op=2), dict(mode=0),
                        dict(flags=8), dict(flags=7), dict(session=bytes(16)),
                        dict(flags=3, sequence=0, result=1), dict(flags=3, sequence=1, result=2),
                        dict(sequence=1), dict(result=-2)):
            with self.subTest(changes=changes), self.assertRaises(hid.BridgeHidError):
                hid.decode_status(status_report(**changes))
        for value in (status_report()[1:], b"\x01" + status_report()[1:], status_report() + b"\0"):
            with self.assertRaises(hid.BridgeHidError):
                hid.decode_status(value)

    def test_bad_commands_rejected_before_transport(self):
        for session in (bytes(16), b"short", "xx", "01"):
            with self.assertRaises(hid.BridgeHidError):
                hid.encode_storage_request(session, 1)
        for sequence in (0, -1, 1 << 32, True, 1.5):
            with self.assertRaises(hid.BridgeHidError):
                hid.encode_storage_request(SESSION, sequence)

    def test_path_filter_is_exact_and_hid_only(self):
        self.assertTrue(hid._EXPECTED_PATH.match(PATH))
        for path in (PATH.replace("054c", "054d"), PATH.replace("01cb", "01cba"),
                     PATH.replace("hid#", "usb#"), r"\\.\PhysicalDrive1", "x" + PATH):
            self.assertFalse(hid._EXPECTED_PATH.match(path))

    def test_msc_diagnostic_pid_requires_explicit_read_only_selection(self):
        msc_path = PATH.replace('pid_01cb', 'pid_01cc')
        self.assertTrue(hid.expected_product_pattern().match(PATH))
        self.assertFalse(hid.expected_product_pattern().match(msc_path))
        self.assertTrue(hid.expected_product_pattern(0x01CC).match(msc_path))
        self.assertFalse(hid.expected_product_pattern(0x01CC).match(PATH))
        for product in (True, 0x02D2, '01CC', -1):
            with self.assertRaises(hid.BridgeHidError):
                hid.expected_product_pattern(product)
        api = FakeHid(hid.decode_status(status_report()))
        api.paths = lambda: [msc_path]
        with patch.object(hid.os, 'name', 'nt'), \
             patch.object(hid, '_WindowsHid', return_value=api) as constructor:
            result = hid.probe(expected_product=0x01CC)
        constructor.assert_called_once_with(expected_product=0x01CC)
        self.assertEqual(result['devices'][0]['path'], msc_path)
        self.assertEqual(api.sent, [])

    def test_msc_native_api_rejects_wrong_pid_and_never_permits_mutation(self):
        msc_path = PATH.replace('pid_01cb', 'pid_01cc')
        api = hid._WindowsHid.__new__(hid._WindowsHid)
        api.expected_product = 0x01CC
        api.expected_path = hid.expected_product_pattern(0x01CC)
        api.kernel, api.hid = Mock(), Mock()
        api.kernel.CreateFileW.return_value = 123
        def attrs(_handle, pointer):
            value = hid.C.cast(pointer, hid.C.POINTER(hid.HIDD_ATTRIBUTES)).contents
            value.VendorID, value.ProductID = 0x054C, 0x01CB
            return True
        api.hid.HidD_GetAttributes.side_effect = attrs
        with self.assertRaises(hid.BridgeHidError):
            with api.open(msc_path):
                self.fail('Wrong PID reached the feature read')
        api.kernel.CloseHandle.assert_called_once_with(123)
        api.hid.HidD_GetPreparsedData.assert_not_called()
        with self.assertRaises(hid.BridgeHidError):
            with api.open(msc_path, write=True):
                self.fail('Diagnostic opened for mutation')
        with self.assertRaises(hid.BridgeHidError):
            api.set(123, status_report())
        api.hid.HidD_SetFeature.assert_not_called()

    def test_session_is_rechecked_before_mutation_on_same_handle(self):
        api = FakeHid(hid.decode_status(status_report()))
        with patch.object(hid, "_WindowsHid", return_value=api):
            result = hid.request_storage(PATH, SESSION, 17)
        self.assertEqual(api.events, [("open", PATH, True), ("get", 123), ("set", 123), ("close",)])
        self.assertEqual(api.sent, [hid.encode_storage_request(SESSION, 17)])
        self.assertTrue(result["sent"])
        self.assertFalse(result["storage_confirmed"])

    def test_stale_session_or_disconnected_never_sends(self):
        for report in (status_report(session=b"x" * 16), status_report(flags=0)):
            api = FakeHid(hid.decode_status(report))
            with patch.object(hid, "_WindowsHid", return_value=api), self.assertRaises(hid.BridgeHidError):
                hid.request_storage(PATH, SESSION, 17)
            self.assertEqual(api.sent, [])

    def test_duplicate_is_idempotent_and_different_pending_sequence_rejected(self):
        for flags, result in ((3, 1), (5, 2)):
            api = FakeHid(hid.decode_status(status_report(flags=flags, sequence=17, result=result)))
            with patch.object(hid, "_WindowsHid", return_value=api):
                outcome = hid.request_storage(PATH, SESSION, 17)
                with self.assertRaises(hid.BridgeHidError):
                    hid.request_storage(PATH, SESSION, 18)
            self.assertTrue(outcome["already_accepted"])
            self.assertFalse(outcome["storage_confirmed"])
            self.assertEqual(api.sent, [])

    def test_feature_extension_keeps_fusa_input_and_output_widths(self):
        source = (Path(__file__).parents[1] / "homebrew/bridge-hid/report_descriptor.h").read_text()
        descriptor = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-f]{2})", source))
        report_size = report_count = 0
        bits = {0x80: 0, 0x90: 0, 0xB0: 0}
        report_ids = []
        i = 0
        while i < len(descriptor):
            tag = descriptor[i]
            n = (0, 1, 2, 4)[tag & 3]
            value = int.from_bytes(descriptor[i+1:i+1+n], "little")
            if tag == 0x75:
                report_size = value
            elif tag == 0x95:
                report_count = value
            elif tag == 0x85:
                report_ids.append(value)
            elif tag & 0xFC in bits:
                bits[tag & 0xFC] += report_size * report_count
            i += n + 1
        self.assertEqual(bits, {0x80: 64, 0x90: 32, 0xB0: 256})
        self.assertEqual(report_ids, [])


if __name__ == "__main__":
    unittest.main()
