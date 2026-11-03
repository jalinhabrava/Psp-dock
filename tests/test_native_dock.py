import contextlib
import ctypes as C
from dataclasses import asdict, replace
from itertools import permutations
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import subprocess
import threading

import native_dock as native
from connection_diagnostics import ConnectionDiagnostics
from bridge_hid import VID
from psp_usb import PspStorage

SESSION = '1234567890abcdef1234567890abcdef'
PATH = r'\\?\hid#vid_054c&pid_01cd&mi_01#7&cafe&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
PARENT = r'USB\VID_054C&PID_01CD\DOCK0300'
FIRMWARES = ((0x0303, 0x03030001), (0x0304, 0x03040001),
             (0x0305, 0x03050001), (0x0306, 0x03060001), (0x0307, 0x03070001),
             (0x0308, 0x03080001))
FIRMWARE_CAPABILITIES = {0x0303: 7, 0x0304: 7, 0x0305: 15, 0x0306: 15, 0x0307: 15, 0x0308: 15}


def status(phase=native.STORAGE, sequence=0, result=0, **changes):
    changes.setdefault('capabilities', FIRMWARE_CAPABILITIES.get(
        changes.get('firmware_revision', native.REVISION), native.CAPABILITIES))
    value = native.NativeStatus(SESSION, phase, sequence, result, True, False, True, False,
                               host_confirmed=True, activity=native.PHASE_ACTIVITIES.get(phase, (0,))[0])
    return replace(value, **changes)


def report(value):
    flags = (native.CONNECTED if value.connected else 0) | (native.PENDING if value.pending else 0)
    flags |= (native.SONY_STORAGE if value.sony_storage else 0) | (native.EXIT_REQUESTED if value.exit_requested else 0)
    flags |= native.HOST_CONFIRMED if value.host_confirmed else 0
    flags |= native.SHUTDOWN_REQUESTED if value.shutdown_requested else 0
    flags |= native.UI_SOUNDS_READY if value.ui_sounds_ready else 0
    return (b'\0' + native.FRAME.pack(native.MAGIC, value.protocol_version, native.STATUS, value.phase,
                                    flags, bytes.fromhex(value.session), value.sequence, value.result) +
            native.EXTENSION.pack(native.EXT_MAGIC, value.firmware_revision, value.capabilities, value.build_id,
                                  value.activity, value.unit, value.transport_word, value.completed, value.total))


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
            ext = native.EXTENSION.unpack(payload[33:65])
            previous = self.frames[-1]
            changes = {'activity': ext[4], 'unit': ext[5], 'completed': ext[7], 'total': ext[8]} if op == native.HOST_PROGRESS else {}
            self.frames = [status(phase, sequence, 2, session=session.hex(),
                                  exit_requested=previous.exit_requested,
                                  protocol_version=previous.protocol_version,
                                  firmware_revision=previous.firmware_revision,
                                  capabilities=previous.capabilities, build_id=previous.build_id,
                                  **changes)]


class ProtocolTests(unittest.TestCase):
    def test_status_roundtrip_and_psp_exit_request(self):
        expected = status(native.CONTROLLER, 3, 2, exit_requested=True)
        self.assertEqual(native.decode_status(report(expected)), expected)

    def test_current_launch_sound_export_flag_is_0308_only(self):
        ready = status(ui_sounds_ready=True)
        self.assertEqual(len(report(ready)), 65)
        self.assertEqual(native.decode_status(report(ready)), ready)
        self.assertFalse(native.decode_status(report(status())).ui_sounds_ready)
        for revision, build in FIRMWARES:
            if revision == 0x0308:
                continue
            old = status(firmware_revision=revision, build_id=build, ui_sounds_ready=True)
            with self.subTest(revision=revision), self.assertRaises(native.NativeDockError):
                native.decode_status(report(old))
        unknown = bytearray(report(ready))
        unknown[8] |= 128
        with self.assertRaises(native.NativeDockError):
            native.decode_status(bytes(unknown))

    def test_exact_firmware_capabilities_and_build_are_required_even_with_sony_usb_revision(self):
        invalid = [dict(firmware_revision=revision, build_id=build) for revision, build in (
            (0x0300, 0x03000001), (0x0301, 0x03010001), (0x0302, 0x03020001),
            (0x0309, 0x03090001), (0x0307, 0x03080001), (0x0308, 0x03070001),
            (0x0308, 0x03080002), (0x0303, 0x03040001), (0x0304, 0x03030001),
            (0x0303, 0x03050001), (0x0305, 0x03030001),
            (0x0304, 0x03050001), (0x0305, 0x03040001),
            (0x0303, 0x03030002), (0x0304, 0x03040002), (0x0305, 0x03050002))]
        invalid += [{'protocol_version': 2}, {'protocol_version': 4},
                    {'capabilities': 3}, {'capabilities': 7}, {'capabilities': 31},
                    {'firmware_revision': 0x0303, 'build_id': 0x03030001, 'capabilities': 15},
                    {'firmware_revision': 0x0304, 'build_id': 0x03040001, 'capabilities': 15}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(native.NativeDockError):
                native.decode_status(report(status(**changes)))
        for offset, byte in ((33, ord('X')), (47, 15)):
            malformed = bytearray(report(status()))
            malformed[offset] = byte
            with self.assertRaises(native.NativeDockError):
                native.decode_status(bytes(malformed))

    def test_probe_is_read_only_and_does_not_claim_waiting_firmware(self):
        for revision, build in FIRMWARES:
            with self.subTest(revision=revision):
                waiting = status(host_confirmed=False, activity=native.WAITING,
                                 firmware_revision=revision, build_id=build)
                self.assertEqual(native.decode_status(report(waiting)), waiting)
                api = FakeApi([waiting])
                observation = native.probe_native(api_factory=lambda: api)
                self.assertEqual(observation['usb_revision'], 0x0100)
                self.assertEqual(observation['firmware_revision'], revision)
                self.assertEqual(observation['status']['build_id'], build)
                self.assertFalse(observation['status']['host_confirmed'])
                self.assertEqual(api.commands, [])

    def test_commands_echo_the_validated_firmware_for_begin_phase_and_progress(self):
        operations = ((native.HOST_BEGIN, native.STORAGE, {}),
                      (native.SET_PHASE, native.CONTROLLER, {}),
                      (native.HOST_PROGRESS, native.STORAGE,
                       {'activity': native.SYNC_IN, 'unit': native.BYTES, 'completed': 3, 'total': 10}))
        for revision, build in FIRMWARES:
            for operation, phase, progress in operations:
                with self.subTest(revision=revision, operation=operation):
                    before = status(firmware_revision=revision, build_id=build)
                    if operation == native.HOST_BEGIN:
                        before = replace(before, host_confirmed=False, activity=native.WAITING)
                    api = FakeApi([before])
                    result = native.request_command(PATH, SESSION, phase, operation,
                                                    api_factory=lambda: api, **progress)
                    self.assertTrue(result['accepted'])
                    self.assertEqual(len(api.commands), 1)
                    frame = native.FRAME.unpack(api.commands[0][1:33])
                    self.assertEqual(frame[1:4], (3, operation, phase))
                    self.assertEqual(native.EXTENSION.unpack(api.commands[0][33:65])[:4],
                                     (b'NAT3', revision, FIRMWARE_CAPABILITIES[revision], build))

    def test_command_rejects_a_supported_but_different_identity_during_ack(self):
        for original, changed in permutations(FIRMWARES, 2):
            for pending in (False, True):
                with self.subTest(original=original, changed=changed, pending=pending):
                    before = status(firmware_revision=original[0], build_id=original[1])
                    foreign_ack = status(native.STORAGE if pending else native.CONTROLLER,
                                         1, 1 if pending else 2, pending=pending,
                                         firmware_revision=changed[0], build_id=changed[1])
                    final_ack = status(native.CONTROLLER, 1, 2,
                                       firmware_revision=original[0], build_id=original[1])
                    api = FakeApi([before, foreign_ack, final_ack], apply=False)
                    with self.assertRaisesRegex(native.NativeDockError, 'identidad del firmware'):
                        native.request_phase(PATH, SESSION, native.CONTROLLER, api_factory=lambda: api)
                    self.assertEqual(len(api.commands), 1)
                    self.assertEqual(api.frames, [final_ack])

    def test_unverified_identity_is_rejected_before_sending(self):
        for revision, build in ((0x0302, 0x03020001), (0x0309, 0x03090001),
            (0x0307, 0x03080001), (0x0308, 0x03070001), (0x0308, 0x03080002),
                                (0x0303, 0x03040001), (0x0304, 0x03030001),
                                (0x0304, 0x03050001), (0x0305, 0x03040001)):
            with self.subTest(revision=revision, build=build):
                api = FakeApi([status(firmware_revision=revision, build_id=build)])
                with self.assertRaises(native.NativeDockError):
                    native.request_phase(PATH, SESSION, native.CONTROLLER, api_factory=lambda: api)
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
        self.assertEqual(native.EXTENSION.unpack(frame[33:65]),
                         (b'NAT3', 0x0308, 15, 0x03080001, 0, 0, 0, 0, 0))

    def test_command_encoding_admits_only_exact_explicit_firmware_pairs(self):
        for revision, build in FIRMWARES:
            with self.subTest(revision=revision):
                frame = native.encode_command(SESSION, 1, native.STORAGE, native.HOST_BEGIN,
                                              firmware_revision=revision, build_id=build)
                self.assertEqual(native.EXTENSION.unpack(frame[33:65])[:4],
                                 (b'NAT3', revision, FIRMWARE_CAPABILITIES[revision], build))
        invalid = [dict(firmware_revision=revision, build_id=build) for revision, build in (
            (0x0302, 0x03020001), (0x0309, 0x03090001),
            (0x0307, 0x03080001), (0x0308, 0x03070001), (0x0308, 0x03080002),
            (0x0303, 0x03040001), (0x0304, 0x03030001),
            (0x0303, 0x03050001), (0x0305, 0x03030001),
            (0x0304, 0x03050001), (0x0305, 0x03040001),
            (0x0303, 0x03030002), (0x0304, 0x03040002), (0x0305, 0x03050002),
            (float(0x0304), 0x03040001), (0x0304, float(0x03040001)),
            (True, 0x03040001), (0x0304, None))]
        invalid += [{'firmware_revision': 0x0303}, {'build_id': 0x03030001}]
        for identity in invalid:
            with self.subTest(identity=identity), self.assertRaises(native.NativeDockError):
                native.encode_command(SESSION, 1, native.STORAGE, native.HOST_BEGIN, **identity)

    def test_phase_command_rejects_unknown_or_bool(self):
        for seq, phase in [(0, 1), (True, 1), (2**32, 1), (1, True), (1, 255)]:
            with self.subTest(seq=seq, phase=phase), self.assertRaises(native.NativeDockError):
                native.encode_phase(SESSION, seq, phase)

    def test_shutdown_encoding_requires_0305_capability_and_safe_target(self):
        for revision, build in FIRMWARES:
            with self.subTest(revision=revision):
                if revision < 0x0305:
                    with self.assertRaises(native.NativeDockError):
                        native.encode_command(SESSION, 1, native.SAFE, native.HOST_SHUTDOWN,
                                              firmware_revision=revision, build_id=build)
                    continue
                frame = native.encode_command(SESSION, 1, native.SAFE, native.HOST_SHUTDOWN,
                                              firmware_revision=revision, build_id=build)
                self.assertEqual(len(frame), 65)
                self.assertEqual(native.FRAME.unpack(frame[1:33]),
                    (b'PDK2', 3, 5, native.SAFE, 0, bytes.fromhex(SESSION), 1, 0))
                self.assertEqual(native.EXTENSION.unpack(frame[33:65]),
                    (b'NAT3', revision, 15, build, 0, 0, 0, 0, 0))
        for phase in (native.STORAGE, native.CONTROLLER, native.COPYING, native.EXIT_SYNC):
            with self.subTest(phase=phase), self.assertRaises(native.NativeDockError):
                native.encode_command(SESSION, 1, phase, native.HOST_SHUTDOWN)

    def test_shutdown_status_flag_requires_current_applied_terminal_ack(self):
        unclaimed = status(native.SAFE, 1, 2, host_confirmed=False, activity=native.WAITING,
                           shutdown_requested=True)
        claimed = status(native.SAFE, 4, 2, shutdown_requested=True)
        for expected in (unclaimed, claimed):
            with self.subTest(claimed=expected.host_confirmed):
                self.assertEqual(native.decode_status(report(expected)), expected)
                self.assertTrue(report(expected)[8] & 32)
        invalid = [replace(unclaimed, phase=native.STORAGE),
                   replace(unclaimed, phase=native.ERROR),
                   replace(unclaimed, sequence=0, result=0),
                   replace(unclaimed, result=1, pending=True),
                   replace(unclaimed, result=-6),
                   replace(unclaimed, activity=native.INVENTORY)]
        invalid.extend(replace(unclaimed, firmware_revision=revision,
                               capabilities=7, build_id=build)
                       for revision, build in FIRMWARES if revision < 0x0305)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(native.NativeDockError):
                native.decode_status(report(value))

    def test_generic_command_cannot_bypass_verified_media_release(self):
        api = FakeApi([status(native.SAFE, 4, 2)])
        constructed = []
        def factory():
            constructed.append(True)
            return api
        with self.assertRaisesRegex(native.NativeDockError, 'expulsión verificada'):
            native.request_command(PATH, SESSION, native.SAFE, native.HOST_SHUTDOWN,
                                   api_factory=factory)
        self.assertEqual(constructed, [])
        self.assertEqual(api.commands, [])

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

    def test_begin_timeout_identifies_handshake_and_retains_last_observation(self):
        waiting = status(host_confirmed=False, activity=native.WAITING)
        api = FakeApi([waiting], apply=False)
        ticks = iter([0, .01, .02, 1])
        with self.assertRaises(native.NativeDockError) as raised:
            native.request_begin(PATH, SESSION, api_factory=lambda: api,
                                 timeout=.1, clock=lambda: next(ticks), pause=lambda _: None)
        error = raised.exception
        self.assertIn('inicio de la conexión', error.message)
        self.assertIn('HOST_BEGIN', str(error))
        self.assertEqual(error.details['sequence'], 1)
        self.assertEqual(error.details['last_observed']['sequence'], 0)
        self.assertFalse(error.details['last_observed']['pending'])
        self.assertFalse(error.details['last_observed']['host_confirmed'])
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
        for revision in (0x0300, 0x0100, 0x0302, 0x0301, 0x0303, 0x0304, 0x0305, 0x0306, 0x0307, 0x0308):
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
                if revision in (0x0100, 0x0303, 0x0304, 0x0305, 0x0306, 0x0307, 0x0308):
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


class CommandTimingTests(unittest.TestCase):
    def test_slow_set_is_separate_from_the_full_ack_timeout(self):
        now = [0.0]
        saved = []
        trace = ConnectionDiagnostics(clock=lambda:now[0], persist=lambda value:saved.append(value))
        api = FakeApi([status(host_confirmed=False, activity=native.WAITING)], apply=False)
        send = api.set
        def slow_set(handle, frame):
            now[0] += 3.0
            send(handle, frame)
        api.set = slow_set
        def pause(delay):
            now[0] += delay
        with self.assertRaisesRegex(native.NativeDockError, 'HOST_BEGIN'):
            native.request_begin(PATH, SESSION, api_factory=lambda:api,
                timeout=.1, clock=lambda:now[0], pause=pause, diagnostics=trace)
        spans = trace.snapshot()['stages']
        self.assertEqual(spans['usb.command.set_feature']['last_ms'], 3000.0)
        self.assertEqual(spans['usb.command.set_feature']['errors'], 0)
        self.assertGreaterEqual(spans['usb.command.ack_wait']['last_ms'], 100.0)
        self.assertLess(spans['usb.command.ack_wait']['last_ms'], 150.0)
        self.assertEqual(spans['usb.command.ack_wait']['errors'], 1)
        self.assertGreaterEqual(spans['usb.command.get_poll']['count'], 4)
        self.assertEqual(spans['usb.command.get_initial']['count'], 1)
        self.assertEqual(len(api.commands), 1)
        self.assertFalse(any(row['stage']=='usb.command.get_poll'
                             for value in saved for row in value['events']))

    def test_synchronous_set_failure_is_timed_without_starting_ack_wait(self):
        now = [0.0]
        trace = ConnectionDiagnostics(clock=lambda:now[0])
        api = FakeApi([status()])
        def failed_set(handle, frame):
            now[0] += 4.0
            raise OSError('Win32 SET_FEATURE failed')
        api.set = failed_set
        with self.assertRaisesRegex(OSError, 'SET_FEATURE failed'):
            native.request_phase(PATH, SESSION, native.CONTROLLER,
                                 api_factory=lambda:api, diagnostics=trace)
        spans = trace.snapshot()['stages']
        self.assertEqual(spans['usb.command.set_feature']['last_ms'], 4000.0)
        self.assertEqual(spans['usb.command.set_feature']['errors'], 1)
        self.assertNotIn('usb.command.ack_wait', spans)

    def test_blocked_set_remains_visible_before_ack_budget_starts(self):
        entered, release = threading.Event(), threading.Event()
        trace, failures = ConnectionDiagnostics(), []
        api = FakeApi([status()])
        send = api.set
        def blocked_set(handle, frame):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('fixture did not release SET_FEATURE')
            send(handle, frame)
        api.set = blocked_set
        def request():
            try:
                native.request_phase(PATH, SESSION, native.CONTROLLER,
                                     api_factory=lambda:api, diagnostics=trace)
            except Exception as exc:
                failures.append(exc)
        worker = threading.Thread(target=request)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            view = trace.snapshot()
            self.assertEqual([row['stage'] for row in view['active']], ['usb.command.set_feature'])
            self.assertNotIn('usb.command.ack_wait', view['stages'])
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(api.commands), 1)

    def test_initial_get_and_poll_get_failures_have_distinct_timings(self):
        for failure_at, name in ((1, 'usb.command.get_initial'), (2, 'usb.command.get_poll')):
            with self.subTest(failure_at=failure_at):
                trace = ConnectionDiagnostics()
                api = FakeApi([status()], apply=False)
                read, calls = api.get, [0]
                def failing_get(handle):
                    calls[0] += 1
                    if calls[0] == failure_at:
                        self.assertIn(name, [row['stage'] for row in trace.snapshot()['active']])
                        raise OSError('GET_FEATURE failed')
                    return read(handle)
                api.get = failing_get
                with self.assertRaisesRegex(OSError, 'GET_FEATURE failed'):
                    native.request_phase(PATH, SESSION, native.CONTROLLER,
                                         api_factory=lambda:api, diagnostics=trace)
                spans = trace.snapshot()['stages']
                self.assertEqual(spans[name]['errors'], 1)
                self.assertEqual(len(api.commands), failure_at-1)
                if failure_at == 2:
                    self.assertEqual(spans['usb.command.ack_wait']['errors'], 1)


class TransportTests(unittest.TestCase):
    def setUp(self):
        # Preserve the historical normal-PnP path here. The 0305 media
        # release + terminal command has its own shutdown regression suite.
        self.status = status(firmware_revision=0x0304, capabilities=7, build_id=0x03040001)
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

    def test_bound_sound_export_readiness_is_fresh_and_never_inferred_from_usb(self):
        self.assertFalse(self.transport.current['ui_sounds_ready'])
        self.status = status(ui_sounds_ready=True)
        first = self.transport.observe([{'root': 'G:\\'}])
        self.assertIs(first['ui_sounds_ready'], True)
        self.status = replace(self.status, ui_sounds_ready=False)
        second = self.transport.observe([{'root': 'G:\\'}])
        self.assertIs(second['ui_sounds_ready'], False)
        self.status = status(firmware_revision=0x0307, build_id=0x03070001,
                             ui_sounds_ready=True)
        # Even an injected observation bypassing the byte decoder cannot
        # lend this new claim to a legacy firmware's bound storage.
        legacy = self.transport.observe([{'root': 'G:\\'}])
        self.assertIs(legacy['ui_sounds_ready'], False)

    def test_controller_snapshot_preserves_the_verified_firmware_pair(self):
        for revision, build in FIRMWARES:
            with self.subTest(revision=revision):
                self.status = replace(self.status, firmware_revision=revision, build_id=build,
                                      capabilities=FIRMWARE_CAPABILITIES[revision])
                self.transport.observe([{'root': 'G:\\'}])
                view = self.transport.controller_status()
                self.assertEqual((view['protocol_version'], view['firmware_revision'],
                                  view['capabilities'], view['build_id']),
                                 (3, revision, FIRMWARE_CAPABILITIES[revision], build))

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

    def test_default_begin_receives_diagnostics_and_injected_begin_keeps_its_signature(self):
        trace = ConnectionDiagnostics()
        self.transport.diagnostics = trace
        self.status = replace(self.status, host_confirmed=False, activity=native.WAITING)
        self.transport.begin_session(self.context('storage', 'initial'))
        self.assertEqual(self.commands, ['begin'])  # self.begin accepts no kwargs.
        self.status = replace(self.status, host_confirmed=False, activity=native.WAITING)
        self.transport._begin = native.request_begin
        observed = []
        def wire(path, session, phase, operation, **options):
            observed.append((phase, operation, options.get('diagnostics')))
            return self.begin(path, session)
        with patch('native_dock.request_command', side_effect=wire):
            self.transport.begin_session(self.context('storage', 'initial'))
        self.assertEqual(observed, [(native.STORAGE, native.HOST_BEGIN, trace)])

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

    def test_failed_begin_can_cancel_by_normal_eject_without_repeating_handshake(self):
        self.status = replace(self.status, host_confirmed=False, activity=native.WAITING)
        self.transport.observe([{'root': 'G:\\'}])
        self.veto = True
        with self.assertRaisesRegex(native.NativeDockError, 'Windows mantiene'):
            self.transport.finish_session(cancel=True)
        self.assertIsNotNone(self.transport.current)
        self.assertEqual(self.commands, [])
        self.veto = False
        self.assertTrue(self.transport.finish_session(cancel=True)['verified'])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.ejections, [(self.storage, (7, PARENT))] * 2)
        self.assertIsNone(self.transport.current)

    def test_unconfirmed_pending_begin_cannot_be_reported_as_cancelled(self):
        self.status = replace(self.status, host_confirmed=False, activity=native.WAITING,
                              sequence=1, result=1, pending=True)
        self.transport.observe([{'root': 'G:\\'}])
        with self.assertRaisesRegex(native.NativeDockError, 'orden pendiente'):
            self.transport.finish_session(cancel=True)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.ejections, [])

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
