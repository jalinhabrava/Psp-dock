import ctypes as C
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import inspect_native_dock as inspect
import native_dock as native
from psp_usb import PspStorage


PATH = r'\\?\hid#vid_054c&pid_01cd&mi_01#7&cafe&0&0000#{guid}'
PARENT = {'devinst': 55, 'instance': r'USB\VID_054C&PID_01CD\SERIAL1'}
OTHER = {'devinst': 66, 'instance': r'USB\VID_054C&PID_01CD\SERIAL2'}
FRAME = b'\0' + native.FRAME.pack(b'PDK2', 2, 1, 1, 5, b'x' * 16, 0, 0)
CURRENT_FRAME = (b'\0' + native.FRAME.pack(b'PDK2', 3, 1, 1, 5, b'x' * 16, 0, 0) +
                 native.EXTENSION.pack(b'NAT3', 0x0303, 7, 0x03030001, 0, 0, 0, 0, 0))


class LowLevelFake(inspect.NativeReadOnlyInspector):
    def __init__(self, revision=0x100, feature_bytes=33, frame=FRAME):
        self.revision, self.feature_bytes, self.frame = revision, feature_bytes, frame
        self.calls = []
        self.hid = SimpleNamespace(HidD_GetPreparsedData=self.preparsed,
            HidP_GetCaps=self.caps, HidD_FreePreparsedData=self.free,
            HidD_GetFeature=self.feature)
        self.kernel = SimpleNamespace(CreateFileW=self.create, CloseHandle=self.close)

    def paths(self):
        return [PATH]

    def create(self, *args):
        self.calls.append(('open', args))
        return 42

    def close(self, handle):
        self.calls.append(('close', handle))
        return 1

    def attributes(self, handle):
        return {'vendor_id': 0x54C, 'product_id': 0x1CD, 'revision': self.revision}

    def preparsed(self, handle, data):
        return 1

    def caps(self, data, pointer):
        caps = C.cast(pointer, C.POINTER(inspect.HIDP_CAPS)).contents
        caps.UsagePage, caps.Usage = 1, 4
        caps.InputReportByteLength, caps.OutputReportByteLength = 9, 5
        caps.FeatureReportByteLength = self.feature_bytes
        return 0x00110000

    def free(self, data):
        self.calls.append(('free', None))
        return 1

    def feature(self, handle, buffer, size):
        self.calls.append(('get_feature', size))
        C.memmove(buffer, self.frame, size)
        return 1


class InspectorTests(unittest.TestCase):
    def test_unknown_revision_is_observed_readonly_with_valid_pdk2(self):
        api = LowLevelFake(revision=0x0100)
        result = api.inspect(PATH)
        self.assertEqual(result['revision_hex'], '0100')
        self.assertFalse(result['supported_revision'])
        self.assertEqual(result['revision_status'], 'retired_protocol')
        self.assertFalse(result['retired_revision'])
        self.assertTrue(result['pdk2_valid'])
        self.assertFalse(result['writes_authorized'])
        self.assertFalse(result['can_change_phase'])
        self.assertEqual(result['caps']['feature_bytes'], 33)
        call = next(call for name, call in api.calls if name == 'open')
        self.assertEqual(call[1], 0)
        self.assertEqual(api.calls[-1], ('close', 42))

    def test_supported_revision_does_not_authorize_mutations(self):
        api = LowLevelFake(revision=0x0100, feature_bytes=65, frame=CURRENT_FRAME)
        result = api.inspect(PATH)
        self.assertTrue(result['supported_revision'])
        self.assertEqual(result['revision_status'], 'supported')
        self.assertFalse(result['retired_revision'])
        self.assertFalse(result['writes_authorized'])
        self.assertEqual(result['firmware_revision'], 0x0303)
        self.assertEqual(result['usb_revision'], 0x0100)
        self.assertEqual(result['protocol_version'], 3)
        self.assertFalse(result['status']['host_confirmed'])
        with self.assertRaises(inspect.InspectionError):
            with api.open(PATH, write=True):
                self.fail('write handle opened')
        with self.assertRaises(inspect.InspectionError):
            api.set(42, b'x' * 33)

    def test_0300_remains_observable_but_retired_and_never_authorized(self):
        api = LowLevelFake(revision=0x0300)
        result = api.inspect(PATH)
        self.assertTrue(result['pdk2_valid'])
        self.assertTrue(result['retired_revision'])
        self.assertEqual(result['revision_status'], 'retired')
        self.assertFalse(result['supported_revision'])
        self.assertFalse(result['writes_authorized'])
        self.assertFalse(result['can_change_phase'])
        self.assertEqual(next(args for name, args in api.calls if name == 'open')[1], 0)
        with self.assertRaises(inspect.InspectionError):
            api.set(42, b'\0' * 33)

    def test_0304_diagnostic_firmware_remains_strictly_read_only(self):
        frame = (CURRENT_FRAME[:33] +
                 native.EXTENSION.pack(b'NAT3', 0x0304, 7, 0x03040001, 0, 0, 0, 0, 0))
        api = LowLevelFake(revision=0x0100, feature_bytes=65, frame=frame)
        result = api.inspect(PATH)
        self.assertTrue(result['supported_revision'])
        self.assertEqual(result['firmware_revision'], 0x0304)
        self.assertEqual(result['status']['build_id'], 0x03040001)
        self.assertFalse(result['writes_authorized'])
        self.assertFalse(result['can_change_phase'])
        self.assertEqual(next(args for name,args in api.calls if name == 'open')[1], 0)

    def test_0301_is_retired_and_a_sony_revision_alone_never_becomes_supported(self):
        old = LowLevelFake(revision=0x0301).inspect(PATH)
        self.assertTrue(old['retired_revision'])
        self.assertFalse(old['supported_revision'])
        actual_old = LowLevelFake(revision=0x0100).inspect(PATH)
        self.assertEqual(actual_old['revision_status'], 'retired_protocol')
        self.assertIsNone(actual_old['firmware_revision'])
        self.assertFalse(actual_old['supported_revision'])
        self.assertFalse(actual_old['writes_authorized'])

    def test_unknown_usb_envelope_does_not_authorize_valid_new_firmware(self):
        result = LowLevelFake(revision=0x9999, feature_bytes=65, frame=CURRENT_FRAME).inspect(PATH)
        self.assertTrue(result['pdk2_valid'])
        self.assertFalse(result['supported_revision'])
        self.assertFalse(result['writes_authorized'])

    def test_caps_mismatch_is_reported_without_wrong_feature_request(self):
        api = LowLevelFake(feature_bytes=129)
        result = api.inspect(PATH)
        self.assertEqual(result['caps']['feature_bytes'], 129)
        self.assertFalse(result['descriptor_matches'])
        self.assertFalse(result['pdk2_valid'])
        self.assertFalse(any(name == 'get_feature' for name, _ in api.calls))

    def test_invalid_frame_retains_raw_observation_and_error(self):
        result = LowLevelFake(frame=b'\0' * 33).inspect(PATH)
        self.assertFalse(result['pdk2_valid'])
        self.assertEqual(result['feature_hex'], '00' * 33)
        self.assertTrue(result['errors'])

    def test_parent_binding_requires_exact_instance_and_node(self):
        identity = PspStorage('G:\\', '2958455453', 'USBSTOR\\PSP1', PARENT['instance'], 'SONY PSP')
        def chain(instance):
            node = PARENT if instance.startswith('HID\\') or instance.endswith('PSP1') else OTHER
            return [{'devinst': 1, 'instance': instance}, node]
        result = inspect.inspect_native(api_factory=LowLevelFake, storage_roots=['G:\\'],
            identify_storage=lambda _: (identity, 7), ancestry=chain)
        candidate = result['storage_candidates'][0]
        self.assertTrue(candidate['binding_exact'])
        self.assertEqual(candidate['matches_hid_paths'], [PATH])
        self.assertFalse(candidate['writes_authorized'])
        changed = PspStorage('G:\\', '2958455453', 'USBSTOR\\PSP2', OTHER['instance'], 'SONY PSP')
        result = inspect.inspect_native(api_factory=LowLevelFake, storage_roots=['G:\\'],
            identify_storage=lambda _: (changed, 7), ancestry=chain)
        self.assertFalse(result['storage_candidates'][0]['binding_exact'])

    def test_interface_node_is_never_treated_as_physical_parent(self):
        self.assertIsNone(inspect._physical_parent([
            {'devinst': 1, 'instance': r'USB\VID_054C&PID_01CD&MI_01\SERIAL1'}]))

    def test_real_usb_test_guard_returns_before_constructing_backend(self):
        with patch.dict(os.environ, {'PSP_DOCK_TEST_NO_REAL_USB': '1'}):
            result = inspect.inspect_native()
        self.assertEqual(result['devices'], [])
        self.assertIn('deshabilitada', result['errors'][0])
        self.assertFalse(result['writes_authorized'])

    def test_cli_writes_new_pc_json_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            with patch.object(inspect, 'inspect_native', return_value={'devices': [], 'writes_authorized': False}), patch('builtins.print'):
                self.assertEqual(inspect.main(['--output', str(output)]), 2)
                self.assertFalse(json.loads(output.read_text())['writes_authorized'])
                self.assertEqual(inspect.main(['--output', str(output)]), 1)


if __name__ == '__main__':
    unittest.main()
