import contextlib
from dataclasses import asdict, replace
import unittest

import native_dock as native
from bridge_hid import VID
from psp_usb import PspStorage

SESSION = '1234567890abcdef1234567890abcdef'
PATH = r'\\?\hid#vid_054c&pid_01cd&mi_01#7&cafe&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
PARENT = r'USB\VID_054C&PID_01CD\DOCK0300'


def status(phase=native.STORAGE, sequence=0, result=0, **changes):
    value = native.NativeStatus(SESSION, phase, sequence, result, True, False, True, False)
    return replace(value, **changes)


def report(value):
    flags = (native.CONNECTED if value.connected else 0) | (native.PENDING if value.pending else 0)
    flags |= (native.SONY_STORAGE if value.sony_storage else 0) | (native.EXIT_REQUESTED if value.exit_requested else 0)
    return b'\0' + native.FRAME.pack(native.MAGIC, 2, native.STATUS, value.phase,
                                    flags, bytes.fromhex(value.session), value.sequence, value.result)


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
        return {'vendor_id': VID, 'product_id': native.PID, 'revision': native.REVISION}

    def get(self, handle):
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0]

    def set(self, handle, payload):
        self.commands.append(payload)
        if self.apply:
            _, _, _, phase, _, session, sequence, _ = native.FRAME.unpack(payload[1:])
            self.frames = [status(phase, sequence, 2, session=session.hex())]


class ProtocolTests(unittest.TestCase):
    def test_status_roundtrip_and_psp_exit_request(self):
        expected = status(native.CONTROLLER, 3, 2, exit_requested=True)
        self.assertEqual(native.decode_status(report(expected)), expected)

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
        self.assertEqual(len(frame), 33)
        self.assertEqual(native.FRAME.unpack(frame[1:]),
                         (b'PDK2', 2, 2, 3, 0, bytes.fromhex(SESSION), 8, 0))

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
            change_phase=self.change, eject=self.eject)
        self.transport.observe([{'root': 'G:\\'}])

    def probe(self):
        return {'path': PATH, 'vendor_id': VID, 'product_id': native.PID,
                'revision': native.REVISION, 'status': asdict(self.status)}

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
