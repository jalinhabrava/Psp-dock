import contextlib
import ctypes as C
from dataclasses import asdict, replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import subprocess

import native_dock as native
from bridge_hid import VID
from psp_usb import PspStorage

SESSION = '1234567890abcdef1234567890abcdef'
PATH = r'\\?\hid#vid_054c&pid_01cd&mi_01#7&cafe&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
PARENT = r'USB\VID_054C&PID_01CD\DOCK0300'


def status(phase=native.STORAGE, sequence=0, result=0, **changes):
    value = native.NativeStatus(SESSION, phase, sequence, result, True, False, True, False,
                               host_confirmed=True, activity=native.PHASE_ACTIVITIES.get(phase, (0,))[0])
    return replace(value, **changes)


def report(value):
    flags = (native.CONNECTED if value.connected else 0) | (native.PENDING if value.pending else 0)
    flags |= (native.SONY_STORAGE if value.sony_storage else 0) | (native.EXIT_REQUESTED if value.exit_requested else 0)
    flags |= native.HOST_CONFIRMED if value.host_confirmed else 0
    return (b'\0' + native.FRAME.pack(native.MAGIC, value.protocol_version, native.STATUS, value.phase,
                                    flags, bytes.fromhex(value.session), value.sequence, value.result) +
            native.EXTENSION.pack(native.EXT_MAGIC, value.firmware_revision, value.capabilities, value.build_id,
                                  value.activity, value.unit, 0, value.completed, value.total))


class FakeApi:
    def __init__(self, frames, apply=True):
        self.frames = list(frames)
        self.commands = []
        self.apply = apply

    def paths(self):
        return [PATH]

    @contextlib.contextmanager
    def open(self, path, write=False):
        assert path == PATH
        yield 1

    def attributes(self, handle):
        return {'vendor_id': VID, 'product_id': native.PID, 'revision': 0x0100}

    def get(self, handle):
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0]

    def set(self, handle, payload):
        self.commands.append(payload)
        if self.apply:
            _, _, op, phase, _, session, sequence, _ = native.FRAME.unpack(payload[1:33])
            ext = native.EXTENSION.unpack(payload[33:])
            previous = self.frames[-1]
            changes = {'activity': ext[4], 'unit': ext[5], 'completed': ext[7], 'total': ext[8]} if op == native.HOST_PROGRESS else {}
            self.frames = [status(phase, sequence, 2, session=session.hex(), exit_requested=previous.exit_requested, **changes)]


class ProtocolTests(unittest.TestCase):
    def test_status_roundtrip_and_psp_exit_request(self):
        expected = status(native.CONTROLLER, 3, 2, exit_requested=True)
        self.assertEqual(native.decode_status(report(expected)), expected)

    def test_exact_firmware_capabilities_and_build_are_required_even_with_sony_usb_revision(self):
        for changes in ({'firmware_revision': 0x0301}, {'protocol_version': 2},
                        {'capabilities': 3}, {'capabilities': 15}, {'build_id': 0x03020002}):
            with self.subTest(changes=changes), self.assertRaises(native.NativeDockError):
                native.decode_status(report(status(**changes)))
        for offset, byte in ((33, ord('X')), (47, 1)):
            malformed = bytearray(report(status()))
            malformed[offset] = byte
            with self.assertRaises(native.NativeDockError):
                native.decode_status(bytes(malformed))

    def test_probe_is_read_only_and_does_not_claim_waiting_firmware(self):
        api = FakeApi([status(host_confirmed=False, activity=native.WAITING)])
        observation = native.probe_native(api_factory=lambda: api)
        self.assertEqual(observation['usb_revision'], 0x0100)
        self.assertEqual(observation['firmware_revision'], 0x0302)
        self.assertFalse(observation['status']['host_confirmed'])
        self.assertEqual(api.commands, [])

    def test_runtime_enumeration_error_is_reported_before_a_handle_or_command(self):
        api = FakeApi([status()])
        with patch.object(api, 'paths', side_effect=RuntimeError('enumeration failed')):
            with self.assertRaisesRegex(native.NativeDockError, 'enumeration failed'):
                native.probe_native(api_factory=lambda: api)
        self.assertEqual(api.commands, [])

    def test_begin_confirms_host_and_preserves_early_psp_exit_request(self):
        api = FakeApi([status(host_confirmed=False, activity=native.WAITING, exit_requested=True)])
        result = native.request_begin(PATH, SESSION, api_factory=lambda: api)
        self.assertTrue(result['accepted'])
        self.assertEqual(len(api.commands), 1)
        self.assertEqual(native.FRAME.unpack(api.commands[0][1:33])[2], native.HOST_BEGIN)
        self.assertTrue(api.frames[0].host_confirmed)
        self.assertTrue(api.frames[0].exit_requested)

    def test_phase_and_progress_never_claim_an_unconfirmed_session(self):
        api = FakeApi([status(host_confirmed=False, activity=native.WAITING)])
        for request in (lambda: native.request_phase(PATH, SESSION, 2, api_factory=lambda: api),
                        lambda: native.request_progress(PATH, SESSION, 1, native.INVENTORY,
                                                        api_factory=lambda: api)):
            with self.assertRaises(native.NativeDockError):
                request()
        self.assertEqual(api.commands, [])

    def test_progress_uses_real_64_bit_counts_and_ack_without_changing_phase(self):
        api = FakeApi([status()])
        total = 8 * 1024**3
        result = native.request_progress(PATH, SESSION, 1, native.SYNC_IN,
                                         unit=native.BYTES, completed=5 * 1024**3, total=total,
                                         api_factory=lambda: api)
        self.assertTrue(result['accepted'])
        decoded = native.decode_status(report(api.frames[0]))
        self.assertEqual((decoded.phase, decoded.total, decoded.completed), (1, total, 5 * 1024**3))
        self.assertEqual(native.FRAME.unpack(api.commands[0][1:33])[2], native.HOST_PROGRESS)
        for unit, done, limit, activity in ((native.UNKNOWN,1,0,native.INVENTORY),
                (native.FILES,0,0,native.INVENTORY), (native.BYTES,11,10,native.SYNC_IN),
                (native.BYTES,1,10,native.COPY_GAME)):
            with self.assertRaises(native.NativeDockError):
                native.encode_command(SESSION, 1, native.STORAGE, native.HOST_PROGRESS,
                                      activity=activity, unit=unit, completed=done, total=limit)

    def test_reject_legacy_and_malformed_status(self):
        raw = report(status())
        invalid = [raw[:-1], b'\1' + raw[1:], raw[:1] + b'PDK1' + raw[5:]]
        invalid += [report(status(sequence=0, result=1, pending=True)),
                    report(status(sequence=2, result=1)),
                    report(status(phase=99)), report(status(session='00' * 16))]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(native.NativeDockError):
                native.decode_status(value)

    def test_phase_command_has_session_and_order(self):
        frame = native.encode_phase(SESSION, 8, native.COPYING)
        self.assertEqual(len(frame), 65)
        self.assertEqual(native.FRAME.unpack(frame[1:33]),
                         (b'PDK2', 3, 2, 3, 0, bytes.fromhex(SESSION), 8, 0))
        self.assertEqual(native.EXTENSION.unpack(frame[33:]),
                         (b'NAT3', 0x0302, 7, 0x03020001, 0, 0, 0, 0, 0))

    def test_phase_command_rejects_unknown_or_bool(self):
        for seq, phase in [(0, 1), (True, 1), (2**32, 1), (1, True), (1, 255)]:
            with self.subTest(seq=seq, phase=phase), self.assertRaises(native.NativeDockError):
                native.encode_phase(SESSION, seq, phase)

    def test_phase_ack_uses_one_command(self):
        api = FakeApi([status()])
        result = native.request_phase(PATH, SESSION, native.CONTROLLER, api_factory=lambda: api)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['sequence'], 1)
        self.assertEqual(len(api.commands), 1)

    def test_same_phase_does_not_send_again(self):
        api = FakeApi([status(native.CONTROLLER, 2, 2)])
        self.assertTrue(native.request_phase(PATH, SESSION, 2, api_factory=lambda: api)['already_applied'])
        self.assertEqual(api.commands, [])

    def test_changed_session_never_sends(self):
        api = FakeApi([status(session='ff' * 16)])
        with self.assertRaises(native.NativeDockError):
            native.request_phase(PATH, SESSION, 2, api_factory=lambda: api)
        self.assertEqual(api.commands, [])

    def test_pending_command_never_replays(self):
        api = FakeApi([status(sequence=1, result=1, pending=True)])
        with self.assertRaises(native.NativeDockError):
            native.request_phase(PATH, SESSION, 2, api_factory=lambda: api)
        self.assertEqual(api.commands, [])

    def test_missing_ack_sends_once_then_errors(self):
        api = FakeApi([status()], apply=False)
        ticks = iter([0, .01, .02, 1])
        with self.assertRaises(native.NativeDockError):
            native.request_phase(PATH, SESSION, 2, api_factory=lambda: api,
                                 timeout=.1, clock=lambda: next(ticks), pause=lambda _: None)
        self.assertEqual(len(api.commands), 1)

    def test_rejects_ack_for_other_target(self):
        api = FakeApi([status(), status(native.COPYING, 1, 2)], apply=False)
        with self.assertRaises(native.NativeDockError):
            native.request_phase(PATH, SESSION, 2, api_factory=lambda: api)

    def test_hid_pnp_instance_is_exact(self):
        self.assertEqual(native.hid_instance(PATH), r'HID\VID_054C&PID_01CD&MI_01\7&CAFE&0&0000')
        self.assertFalse(native.USB_PARENT.fullmatch(r'USB\VID_054C&PID_01CD&MI_01\DOCK0300'))
        with self.assertRaises(native.NativeDockError):
            native.hid_instance(PATH.replace('01cd', '01cc'))

    def test_usb_revision_envelope_is_separate_from_firmware_identity(self):
        for revision in (0x0300, 0x0100, 0x0302, 0x0301, 0x0303):
            with self.subTest(revision=revision):
                api = native.NativeHid.__new__(native.NativeHid)
                calls = []
                current = [status()]
                def caps(data, pointer):
                    value = C.cast(pointer, C.POINTER(native.HIDP_CAPS)).contents
                    value.UsagePage, value.Usage = 1, 4
                    value.FeatureReportByteLength = 65
                    value.InputReportByteLength, value.OutputReportByteLength = 9, 5
                    return 0x00110000
                api.kernel = SimpleNamespace(CreateFileW=lambda *args: 42,
                    CloseHandle=lambda handle: calls.append('closed'))
                api.hid = SimpleNamespace(HidD_GetPreparsedData=lambda *args: 1,
                    HidP_GetCaps=caps, HidD_FreePreparsedData=lambda data: None)
                api.attributes = lambda handle: {'vendor_id': VID, 'product_id': native.PID,
                                                'revision': revision}
                api.paths = lambda: [PATH]
                api.get = lambda handle: current[0]
                def command(handle, payload):
                    calls.append('set_feature')
                    current[0] = status(native.CONTROLLER, 1, 2)
                api.set = command
                if revision in (0x0100, 0x0302):
                    self.assertIsNotNone(native.probe_native(api_factory=lambda: api))
                    self.assertTrue(native.request_phase(PATH, SESSION, native.CONTROLLER,
                                    api_factory=lambda: api)['accepted'])
                    self.assertEqual(calls.count('set_feature'), 1)
                else:
                    with self.assertRaises(native.NativeDockError):
                        native.probe_native(api_factory=lambda: api)
                    with self.assertRaises(native.NativeDockError):
                        native.request_phase(PATH, SESSION, native.CONTROLLER, api_factory=lambda: api)
                    self.assertNotIn('set_feature', calls)
                self.assertEqual(calls.count('closed'), 2)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.status = status()
        self.storage = PspStorage('G:\\', '1234', r'USBSTOR\DISK&VEN_SONY&PROD_PSP\SERIAL',
                                  r'USB\VID_054C&PID_01CD&MI_00\SERIAL', 'Sony PSP MS USB Device')
        self.commands, self.ejections = [], []
        self.veto = False
        self.wrong_parent = False
        self.transport = native.NativeDockTransport(probe=self.probe,
            identify_storage=lambda drive: (self.storage, 2), parent_of=self.parent,
            serial_of=lambda drive: self.storage.volume_serial,
            change_phase=self.change, begin=self.begin, progress=self.progress, eject=self.eject)
        self.transport.observe([{'root': 'G:\\'}])

    def probe(self):
        return {'path': PATH, 'vendor_id': VID, 'product_id': native.PID,
                'revision': 0x0100, 'status': asdict(self.status)}

    def begin(self, path, session):
        self.commands.append('begin')
        self.status = replace(self.status, host_confirmed=True, activity=native.INVENTORY,
                              sequence=self.status.sequence + 1, result=2)
        return {'accepted': True}

    def progress(self, path, session, phase, activity, **fields):
        self.commands.append('progress')
        self.status = replace(self.status, activity=activity, **fields)
        return {'accepted': True}

    def parent(self, instance):
        if self.wrong_parent and instance.startswith('USBSTOR'):
            return 9, PARENT + '-OTHER'
        return 7, PARENT

    def change(self, path, session, phase):
        self.commands.append(phase)
        self.status = replace(self.status, phase=phase, sequence=self.status.sequence + 1, result=2)
        return {'accepted': True}

    def eject(self, storage, parent):
        self.ejections.append((storage, parent))
        return {'host_eject_accepted': not self.veto, 'veto_name': 'Explorer' if self.veto else ''}

    def context(self, role, purpose):
        return {'identity': {'session_id': SESSION, 'volume_serial': '1234'},
                'role': role, 'purpose': purpose}

    def test_storage_and_controller_permission_are_distinct(self):
        self.assertTrue(self.transport.current['storage_ready'])
        self.assertFalse(self.transport.current['controller_ready'])
        self.transport.set_phase(native.CONTROLLER)
        self.assertTrue(self.transport.current['storage_mounted'])
        self.assertFalse(self.transport.current['storage_ready'])
        self.assertTrue(self.transport.current['controller_ready'])
        with self.assertRaises(native.NativeDockError):
            self.transport.assert_storage('G:\\')

    def test_bound_storage_is_not_write_permission_until_explicit_begin(self):
        self.status = replace(self.status, host_confirmed=False, activity=native.WAITING)
        self.transport.observe([{'root': 'G:\\'}])
        self.assertFalse(self.transport.controller_status()['connected'])
        with self.assertRaises(native.NativeDockError):
            self.transport.assert_storage('G:\\')
        self.assertEqual(self.commands, [])
        self.transport.begin_session(self.context('storage', 'initial'))
        self.assertEqual(self.commands, ['begin'])
        self.assertEqual(self.transport.assert_storage('G:\\'), self.storage)

    def test_waiting_storage_timeout_is_visible_and_never_claims_host(self):
        self.status = replace(self.status, host_confirmed=False, activity=native.WAITING)
        self.transport._identify = lambda drive: (_ for _ in ()).throw(subprocess.TimeoutExpired('CIM', 30))
        # Force a new binding instead of the existing cached volume binding.
        self.transport._link = None
        with self.assertRaises(native.NativeDockError):
            self.transport.observe([{'root': 'G:\\'}])
        view = self.transport.connection_status()
        self.assertEqual(view['phase'], 'waiting_storage')
        self.assertIn('CIM', view['diagnostics'])
        self.assertEqual(self.commands, [])

    def test_early_cancel_only_changes_phase_and_requests_exact_eject(self):
        result = self.transport.finish_session(cancel=True)
        self.assertTrue(result['verified'])
        self.assertEqual(self.commands, [native.EXIT_SYNC, native.SAFE])
        self.assertEqual(self.ejections, [(self.storage, (7, PARENT))])

    def test_copy_obtains_phase_before_storage_permission(self):
        self.transport.set_phase(native.CONTROLLER)
        self.transport.request_role(self.context('storage', 'game_copy'))
        self.assertEqual(self.commands, [native.CONTROLLER, native.COPYING])
        self.assertEqual(self.transport.assert_storage('G:\\'), self.storage)

    def test_stale_intent_cannot_send(self):
        ctx = self.context('storage', 'exit')
        ctx['identity']['session_id'] = 'ff' * 16
        with self.assertRaises(native.NativeDockError):
            self.transport.request_role(ctx)
        self.assertEqual(self.commands, [])

    def test_wrong_volume_cannot_authorize_write(self):
        self.storage = replace(self.storage, volume_serial='5678')
        with self.assertRaises(native.NativeDockError):
            self.transport.assert_storage('G:\\')
        self.assertIsNone(self.transport.current)

    def test_other_usb_parent_cannot_authorize_write(self):
        self.wrong_parent = True
        with self.assertRaises(native.NativeDockError):
            self.transport.assert_storage('G:\\')

    def test_changed_session_invalidates_controller(self):
        self.transport.set_phase(native.CONTROLLER)
        self.status = replace(self.status, session='ff' * 16)
        with self.assertRaises(native.NativeDockError):
            self.transport.assert_controller(SESSION)

    def test_exit_requires_exit_phase_and_normal_pnp_release(self):
        with self.assertRaises(native.NativeDockError):
            self.transport.finish_session()
        self.transport.request_role(self.context('storage', 'exit'))
        result = self.transport.finish_session()
        self.assertTrue(result['verified'])
        self.assertEqual(self.commands, [native.EXIT_SYNC, native.SAFE])
        self.assertEqual(self.ejections[0][1], (7, PARENT))
        self.assertIsNone(self.transport.current)

    def test_veto_keeps_safe_phase_without_writes_and_can_retry_only_eject(self):
        self.transport.request_role(self.context('storage', 'exit'))
        self.veto = True
        with self.assertRaises(native.NativeDockError):
            self.transport.finish_session()
        self.assertTrue(self.transport.current['safe_ready'])
        self.assertFalse(self.transport.current['storage_ready'])
        with self.assertRaises(native.NativeDockError):
            self.transport.assert_storage('G:\\')
        self.veto = False
        self.assertTrue(self.transport.finish_session()['verified'])
        self.assertEqual(self.commands, [native.EXIT_SYNC, native.SAFE])


if __name__ == '__main__':
    unittest.main()
