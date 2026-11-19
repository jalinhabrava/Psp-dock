import contextlib
from dataclasses import asdict, replace
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import linux_native as linux
import native_dock as native
from linux_input import decode_input, PSPLinuxReader

SESSION = '12' * 16


def status(phase=native.STORAGE, **changes):
    return replace(native.NativeStatus(SESSION, phase, 0, 0, True, False, True, False,
                                      host_confirmed=True, activity=native.PHASE_ACTIVITIES[phase][0]), **changes)


def report(current):
    flags = ((native.CONNECTED if current.connected else 0) |
             (native.SONY_STORAGE if current.sony_storage else 0) |
             (native.HOST_CONFIRMED if current.host_confirmed else 0) |
             (native.PENDING if current.pending else 0) |
             (native.EXIT_REQUESTED if current.exit_requested else 0))
    return (b'\0' + native.FRAME.pack(b'PDK2', current.protocol_version, 1, current.phase,
        flags, bytes.fromhex(current.session), current.sequence, current.result) +
        native.EXTENSION.pack(native.EXT_MAGIC, current.firmware_revision, current.capabilities,
            current.build_id, current.activity, current.unit, 0, current.completed, current.total))


class LinuxFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.sysfs, self.dev = self.root / 'sys', self.root / 'dev'
        self.mountinfo = self.root / 'mountinfo'
        self.card = self.root / 'mounted PSP'
        (self.card / 'PSP').mkdir(parents=True)
        self.usb = self.sysfs / 'devices/pci/usb1/1-2'
        self.usb.mkdir(parents=True)
        for name, value in [('idVendor', '054c'), ('idProduct', '01cd'), ('bcdDevice', '0100')]:
            (self.usb / name).write_text(value)
        self.block = self.usb / '1-2:1.0/host0/target0/block/sdz/sdz1'
        self.block.mkdir(parents=True)
        (self.block / 'uevent').write_text('DEVNAME=sdz1\nMAJOR=8\nMINOR=241\n')
        (self.sysfs / 'dev/block').mkdir(parents=True)
        (self.sysfs / 'dev/block/8:241').symlink_to(self.block, target_is_directory=True)
        (self.dev / 'disk/by-uuid').mkdir(parents=True)
        (self.dev / 'sdz1').touch()
        (self.dev / 'disk/by-uuid/ABCD-1234').symlink_to(self.dev / 'sdz1')
        self.hid = self.usb / '1-2:1.1/0003:054C:01CD.0001'
        self.hid.mkdir(parents=True)
        (self.hid / 'report_descriptor').write_bytes(linux.REPORT_DESCRIPTOR)
        self.hidclass = self.sysfs / 'class/hidraw/hidraw3'
        self.hidclass.mkdir(parents=True)
        (self.hidclass / 'device').symlink_to(self.hid, target_is_directory=True)
        (self.hidclass / 'dev').write_text('240:3')
        self.path = str(self.dev / 'hidraw3')
        self.mount()
        self.commands = []
        self.system = linux.LinuxSystem(sysfs_root=self.sysfs, dev_root=self.dev,
                                         mountinfo=self.mountinfo, run=self.run)

    def mount(self, *, options='rw,nosuid', root='/', filesystem='vfat'):
        escaped = str(self.card).replace(' ', r'\040')
        self.mountinfo.write_text(f'71 25 8:241 {root} {escaped} {options} - {filesystem} /dev/sdz1 rw\n')

    def run(self, args, **kwargs):
        self.commands.append(args)
        self.mountinfo.write_text('')
        return SimpleNamespace(returncode=0)

    def device(self, current):
        return {'path': self.path, 'vendor_id': native.VID, 'product_id': native.PID,
                'revision': int((self.usb / 'bcdDevice').read_text(), 16), 'status': asdict(current)}


class StorageAndTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = LinuxFixture(self.temp.name)
        self.system = self.fixture.system

    def test_identity_matches_fat_serial_used_on_windows_and_same_usb_parent(self):
        card, _ = self.system.identify(self.fixture.card)
        self.assertEqual(card.volume_serial, str(0xABCD1234))
        self.assertEqual(self.system.parent_of(card.disk_id), self.system.parent_of(self.system.hid_identity(self.fixture.path)))
        self.assertEqual(self.system.devices()[0]['root'], str(self.fixture.card))

    def test_revision_and_descriptor_gate_fail_before_any_device_open(self):
        for value in ('0300', '0301', '0303', '0207'):
            (self.fixture.usb / 'bcdDevice').write_text(value)
            self.assertEqual(self.system.paths(), [])
            self.assertEqual(self.system.devices(), [])
        (self.fixture.usb / 'bcdDevice').write_text('0100')
        (self.fixture.hid / 'report_descriptor').write_bytes(linux.REPORT_DESCRIPTOR[:-1])
        self.assertEqual(self.system.paths(), [])
        self.assertEqual(self.fixture.commands, [])

    def test_discovery_does_not_resolve_unrelated_network_or_usb_mount_paths(self):
        self.fixture.mountinfo.write_text(self.fixture.mountinfo.read_text() +
            '81 25 0:44 / /network/unreachable rw - nfs4 server:/share rw\n' +
            '82 25 8:17 / /media/unrelated rw - vfat /dev/sdb1 rw\n')
        original = self.system.identify
        seen = []
        def identify(point):
            seen.append(point)
            self.assertEqual(point, str(self.fixture.card))
            return original(point)
        with patch.object(self.system, 'identify', side_effect=identify):
            self.assertEqual(len(self.system.devices()), 1)
        self.assertEqual(seen, [str(self.fixture.card)])

    def test_no_arbitrary_folders_submounts_readonly_or_nonfat(self):
        for arguments in ({'options': 'ro'}, {'root': '/PSP'}, {'filesystem': 'ext4'}):
            self.fixture.mount(**arguments)
            with self.assertRaises(ValueError):
                self.system.identify(self.fixture.card)
        self.fixture.mount()
        with self.assertRaises(ValueError):
            self.system.identify(self.fixture.card / 'PSP')
        (self.fixture.dev / 'disk/by-uuid/ABCD-1234').unlink()
        with self.assertRaises(ValueError):
            self.system.identify(self.fixture.card)

    def test_linux_path_case_is_not_folded_and_foreign_hid_parent_is_rejected(self):
        transport = linux.create_transport(self.system)
        transport._probe = lambda: self.fixture.device(status())
        transport.observe(self.system.devices())
        with self.assertRaisesRegex(ValueError, 'otra Memory Stick'):
            transport.assert_storage(str(self.fixture.card).upper())
        foreign = self.fixture.sysfs / 'devices/pci/usb1/1-3'
        foreign.mkdir()
        for name in ('idVendor', 'idProduct', 'bcdDevice'):
            (foreign / name).write_text((self.fixture.usb / name).read_text())
        transport._hid_identity = lambda _: str(foreign)
        transport._link = None
        with self.assertRaisesRegex(ValueError, 'mismo padre USB'):
            transport.observe(self.system.devices())

    def test_complete_phase_flow_unmounts_only_after_safe_ack(self):
        transport = linux.create_transport(self.system)
        current = [status()]
        transport._probe = lambda: self.fixture.device(current[0])
        def phase(path, session, target):
            self.assertEqual(path, self.fixture.path)
            self.assertEqual(session, SESSION)
            current[0] = status(target)
            return {'accepted': True}
        transport._change_phase = phase
        transport.observe(self.system.devices())
        transport.set_phase(native.CONTROLLER)
        with self.assertRaises(ValueError):
            transport.assert_storage(self.fixture.card)
        with self.assertRaises(ValueError):
            transport.finish_session()
        self.assertEqual(self.fixture.commands, [])
        transport.set_phase(native.COPYING)
        transport.assert_storage(self.fixture.card)
        transport.set_phase(native.CONTROLLER)
        transport.set_phase(native.EXIT_SYNC)
        result = transport.finish_session()
        self.assertTrue(result['verified'])
        self.assertEqual(current[0].phase, native.SAFE)
        self.assertEqual(self.fixture.commands, [['udisksctl', 'unmount', '--block-device',
                                                  str(self.fixture.dev / 'sdz1'), '--no-user-interaction']])

    def test_eject_veto_timeout_and_still_mounted_never_report_success(self):
        identity, _ = self.system.identify(self.fixture.card)
        parent = self.system.parent_of(identity.disk_id)
        for response in (SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)):
            self.system.run = lambda *a, **k: response
            with self.assertRaises(ValueError):
                self.system.eject(identity, parent)
        self.system.run = lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired('udisksctl', 30))
        with self.assertRaises(ValueError):
            self.system.eject(identity, parent)

    def test_multiple_mounts_same_card_prevent_eject(self):
        identity, _ = self.system.identify(self.fixture.card)
        parent = self.system.parent_of(identity.disk_id)
        self.fixture.mountinfo.write_text(self.fixture.mountinfo.read_text() + '72 25 8:241 / /tmp/other rw - vfat /dev/sdz1 rw\n')
        with self.assertRaisesRegex(ValueError, 'otros montajes'):
            self.system.eject(identity, parent)
        self.assertEqual(self.fixture.commands, [])

    def test_descriptor_exactly_matches_firmware(self):
        path = Path(__file__).resolve().parents[1] / 'homebrew/native-dock/native_report_descriptor.h'
        actual = bytes(int(value, 16) for value in re.findall(r'0x([0-9a-fA-F]{2})', path.read_text()))
        self.assertEqual(linux.REPORT_DESCRIPTOR, actual)

    def test_usb_bcd_device_is_reported_without_claiming_a_firmware_revision(self):
        api = linux.LinuxHid(self.system)
        api.handles[17] = self.fixture.path
        self.assertEqual(api.attributes(17)['revision'], 0x0100)
        (self.fixture.usb / 'bcdDevice').write_text('0302')
        self.assertEqual(api.attributes(17)['revision'], 0x0302)
        # A retired 32-byte feature descriptor is rejected even if Sony
        # still publishes the otherwise admissible bcdDevice 0100.
        (self.fixture.usb / 'bcdDevice').write_text('0100')
        (self.fixture.hid / 'report_descriptor').write_bytes(
            linux.REPORT_DESCRIPTOR.replace(b'\x95\x40\xb1', b'\x95\x20\xb1'))
        self.assertEqual(self.system.paths(), [])

    def test_linux_begin_and_progress_callbacks_use_one_65_byte_command_each(self):
        current = [status(host_confirmed=False, activity=native.WAITING)]
        commands = []
        fixture = self.fixture
        class Api:
            def paths(self):
                return [fixture.path]
            @contextlib.contextmanager
            def open(self, path, write=False):
                self.path = path
                yield 17
            def attributes(self, fd):
                return {'vendor_id': native.VID, 'product_id': native.PID, 'revision': 0x0100}
            def get(self, fd):
                return native.decode_status(report(current[0]))
            def set(self, fd, payload):
                self.assert_length = len(payload)
                commands.append(payload)
                _, version, op, phase, _, nonce, seq, _ = native.FRAME.unpack(payload[1:33])
                extension = native.EXTENSION.unpack(payload[33:])
                activity, unit, done, total = extension[4], extension[5], extension[7], extension[8]
                current[0] = status(phase, sequence=seq, result=2,
                    activity=activity if op == native.HOST_PROGRESS else native.PHASE_ACTIVITIES[phase][0],
                    unit=unit, completed=done, total=total)
        with patch('linux_native.LinuxHid', return_value=Api()):
            transport = linux.create_transport(self.system)
            observation = transport.observe(self.system.devices())
            self.assertEqual(observation['usb_revision'], 0x0100)
            self.assertEqual(observation['firmware_revision'], 0x0302)
            with self.assertRaises(ValueError):
                transport.assert_storage(self.fixture.card)
            with self.assertRaises(ValueError):
                transport.set_phase(native.CONTROLLER)
            self.assertEqual(commands, [])
            context = {'identity': {'session_id': SESSION, 'volume_serial': observation['volume_serial']}}
            self.assertTrue(transport.begin_session(context)['accepted'])
            transport.assert_storage(self.fixture.card)
            transport.set_progress(context, native.CHOOSING)
            self.assertTrue(transport.controller_status()['connected'])
            transport.set_progress(context, native.SYNC_IN, unit=native.BYTES, completed=25, total=100)
            self.assertEqual(transport.current['completed'], 25)
            self.assertFalse(transport.controller_status()['connected'])
            with self.assertRaises(ValueError):
                transport.set_progress(context, native.COPY_GAME)
        self.assertEqual([len(command) for command in commands], [65, 65, 65])
        self.assertEqual([command[6] for command in commands], [native.HOST_BEGIN, native.HOST_PROGRESS, native.HOST_PROGRESS])

    def test_ioctl_opens_only_verified_character_device_and_closes_on_swap(self):
        api = linux.LinuxHid(self.system)
        with patch('os.open', return_value=17), patch('os.close') as close, patch('os.fstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_rdev=0)):
            with self.assertRaises(ValueError):
                with api.open(self.fixture.path):
                    self.fail('regular file accepted as hidraw')
        close.assert_called_once_with(17)

    def test_usb_disabled_default_discovery_does_not_open_sysfs_or_dev(self):
        with patch.dict(os.environ, {'PSP_DOCK_TEST_NO_REAL_USB': '1'}), patch('os.open', side_effect=AssertionError('physical device')):
            self.assertEqual(linux.LinuxSystem().devices(), [])
            self.assertEqual(linux.LinuxHid().paths(), [])
            self.assertIsNone(linux.create_transport().observe([]))


class InputAndIoctlTests(unittest.TestCase):
    def test_wire_layout_controls_and_diagonal(self):
        # X + TRIANGLE + SELECT + HOME, with NE hat.
        buttons = (1 << 0) | (1 << 3) | (1 << 6) | (1 << 8)
        report = bytes([127, 127, 127, 127, 2 | ((buttons & 15) << 4), buttons >> 4, 0, 0])
        frame = decode_input(report)
        self.assertTrue(all(frame[key] for key in ('accept', 'graphics', 'exit', 'up', 'right')))
        self.assertFalse(frame['back'])
        self.assertEqual(frame['button_mask'], buttons)
        self.assertEqual(frame['source'], 'psp_linux_hidraw')
        for bad in (report + b'\0', report[:-1], report[:4] + b'\x09\0\0\0'):
            with self.assertRaises(ValueError):
                decode_input(bad)

    def test_feature_ioctl_includes_zero_report_id_and_requires_full_transfer(self):
        import fcntl
        api = linux.LinuxHid()
        current = status()
        payload = report(current)
        def ioctl(fd, request, buffer, mutate):
            self.assertEqual(len(buffer), 65)
            self.assertEqual(buffer[0], 0)
            self.assertTrue(mutate)
            if request == linux.feature_ioctl(7):
                buffer[:] = payload
            return 65
        with patch.object(fcntl, 'ioctl', side_effect=ioctl):
            self.assertEqual(api.get(10), current)
            api.set(10, native.encode_phase(SESSION, 1, native.CONTROLLER))
        with patch.object(fcntl, 'ioctl', return_value=64):
            with self.assertRaises(ValueError):
                api.get(10)
            with self.assertRaises(ValueError):
                api.set(10, native.encode_phase(SESSION, 1, native.CONTROLLER))

    def test_reader_preserves_held_input_but_releases_on_phase_change(self):
        current = [status(activity=native.CHOOSING)]
        closed = []
        class Api:
            @contextlib.contextmanager
            def open(self, path):
                try:
                    yield 10
                finally:
                    closed.append(path)
            def attributes(self, fd):
                return {}
            def get(self, fd):
                return current[0]
        reports = [bytes([127, 127, 127, 127, 16, 0, 0, 0])]
        def read(fd, size):
            if reports:
                return reports.pop(0)
            raise BlockingIOError()
        reader = PSPLinuxReader(api_factory=Api, read=read)
        self.addCleanup(reader.close)
        observed = {'path': '/dev/hidraw3', 'session_id': SESSION, 'usb_parent': '/sys/fake'}
        self.assertTrue(reader.read(observed)['accept'])
        self.assertTrue(reader.read(observed)['accept'])
        current[0] = status(native.COPYING)
        with self.assertRaises(ValueError):
            reader.read(observed)
        self.assertEqual(closed, ['/dev/hidraw3'])
        current[0] = status(activity=native.CHOOSING)
        self.assertFalse(reader.read(observed)['accept'])
        for value in (status(host_confirmed=False, activity=native.WAITING),
                      status(activity=native.INVENTORY), status(activity=native.SYNC_IN)):
            current[0] = value
            with self.assertRaises(ValueError):
                reader.read(observed)

    def test_extended_feature_rejects_retired_firmware_wrong_build_and_capabilities(self):
        for invalid in (status(firmware_revision=0x0300), status(firmware_revision=0x0301),
                        status(build_id=native.BUILD_ID + 1), status(capabilities=1),
                        status(protocol_version=2)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                native.decode_status(report(invalid))


if __name__ == '__main__':
    unittest.main()
