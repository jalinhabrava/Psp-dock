"""Current 0308 exit/recovery contract using synthetic mountinfo and HID only."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import patch

from psp_dock.devices import native_dock as native
from psp_dock.devices.linux_shutdown import LinuxShutdown, durable_json
from tests.test_linux_native import LinuxFixture, SESSION, status


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class LinuxShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = LinuxFixture(self.temp.name)
        self.system = self.fixture.system
        self.identity, _ = self.system.identify(self.fixture.card)
        self.path = Path(self.temp.name) / 'data/native-close-linux.json'
        self.events, self.commands = [], []
        self.now = 0
        self.removed = False
        self.remove_on_set = True
        self.fail_stage = None
        self.current_status = status(native.SAFE, sequence=7, result=2)
        self.current = dict(path=self.fixture.path, session_id=SESSION,
            volume_serial=self.identity.volume_serial, root=self.identity.drive,
            usb_parent=self.identity.usb_device_id, protocol_version=native.VERSION,
            firmware_revision=native.REVISION, capabilities=native.CAPABILITIES,
            build_id=native.BUILD_ID, sequence=7, phase=native.SAFE,
            host_confirmed=True)
        owner = self

        class Api:
            @contextmanager
            def open(self, path, write=False):
                owner.assertEqual(path, owner.fixture.path)
                owner.events.append(('open', write))
                yield 1

            def get(self, _handle):
                owner.events.append(('get',))
                return owner.current_status

            def set(self, _handle, command):
                owner.events.append(('set',))
                owner.commands.append(command)
                sequence = native.FRAME.unpack(command[1:33])[6]
                owner.current_status = replace(owner.current_status, sequence=sequence,
                    pending=True, shutdown_requested=True, result=1)
                owner.removed = owner.remove_on_set

        self.api = Api()
        self.system.run = self.unmount
        self.close = self.coordinator()

    def coordinator(self, **options):
        return LinuxShutdown(self.path, system=self.system, api_factory=lambda: self.api,
            removed=lambda parent, path: self.removed, persist=self.persist,
            clock=lambda: self.now, pause=self.pause, **options)

    def persist(self, path, record):
        self.events.append(('persist', record['stage'], (record.get('release') or {}).get('status')))
        if record['stage'] == self.fail_stage:
            raise OSError('checkpoint failed')
        durable_json(path, record)

    def pause(self, _delay):
        self.now += 0.5

    def unmount(self, args, **kwargs):
        self.events.append(('unmount',))
        return self.fixture.run(args, **kwargs)

    def prepare(self):
        self.close.prepare(self.current, self.identity)

    def finish(self):
        return self.close.finish(parent_of=self.system.parent_of)

    def test_0308_exit_persists_unmount_receipt_before_terminal_then_requires_removal(self):
        self.prepare()
        result = self.finish()
        self.assertTrue(result['verified'] and result['storage_released'] and result['device_removed'])
        self.assertEqual(result['eject_kind'], 'unmount')
        self.assertEqual(self.commands, [native.encode_command(SESSION, 8, native.SAFE, native.HOST_SHUTDOWN)])
        self.assertEqual(self.fixture.commands, [['udisksctl', 'unmount', '--block-device',
            str(self.fixture.dev / 'sdz1'), '--no-user-interaction']])
        self.assertLess(self.events.index(('persist', 'release_pending', None)), self.events.index(('unmount',)))
        self.assertLess(self.events.index(('persist', 'release_pending', 'eject_requested')), self.events.index(('unmount',)))
        self.assertLess(self.events.index(('persist', 'sending', 'unmounted')), self.events.index(('set',)))
        record = json.loads(self.path.read_text())
        self.assertEqual(record['stage'], 'complete')
        self.assertTrue(record['release']['release_check']['same_device'])
        self.assertNotIn('media_absent', record['release']['release_check'])
        self.assertEqual(self.coordinator().record['stage'], 'complete')

    def test_unclaimed_cancel_uses_terminal_only_without_begin_or_file_work(self):
        self.current.update(sequence=0, phase=native.STORAGE, host_confirmed=False)
        self.current_status = status(host_confirmed=False, activity=native.WAITING)
        self.close.prepare(self.current, self.identity, cancel=True)
        self.finish()
        self.assertEqual(self.commands, [native.encode_command(SESSION, 1, native.SAFE, native.HOST_SHUTDOWN)])
        self.assertTrue(self.close.record['cancel'])

    def test_udisks_veto_never_sends_terminal_and_explicit_retry_can_unmount(self):
        self.prepare()
        self.system.run = lambda *args, **kw: SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(ValueError, 'rechazó'):
            self.finish()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.close.record['release']['status'], 'release_failed')
        self.system.run = self.unmount
        self.assertTrue(self.finish()['verified'])
        self.assertEqual(len(self.commands), 1)

    def test_timeout_after_actual_unmount_recovers_from_receipt_without_repeating_udisks(self):
        self.prepare()
        def uncertain(*args, **kwargs):
            self.unmount(*args, **kwargs)
            raise subprocess.TimeoutExpired('udisksctl', 30)
        self.system.run = uncertain
        with self.assertRaisesRegex(ValueError, 'conserva el recibo'):
            self.finish()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.close.record['release']['status'], 'eject_requested')
        self.close = self.coordinator()
        self.system.run = lambda *args, **kwargs: self.fail('uncertain unmount was repeated')
        self.assertTrue(self.finish()['verified'])
        self.assertEqual(len(self.fixture.commands), 1)

    def test_uncertain_unmount_still_mounted_stays_blocked_without_replay(self):
        self.prepare()
        self.system.run = lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired('udisksctl', 30))
        with self.assertRaises(ValueError):
            self.finish()
        self.close = self.coordinator()
        self.system.run = lambda *args, **kwargs: self.fail('uncertain unmount was repeated')
        with self.assertRaisesRegex(ValueError, 'sigue montada'):
            self.finish()
        self.assertEqual(self.commands, [])

    def test_mount_reappearing_after_unmount_blocks_retry_of_terminal_command(self):
        self.prepare()
        self.remove_on_set = False
        with self.assertRaisesRegex(ValueError, 'no confirmó el cierre'):
            self.finish()
        self.fixture.mount()
        with self.assertRaisesRegex(ValueError, 'sigue montada'):
            self.finish()
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(len(self.fixture.commands), 1)

    def test_pending_terminal_is_observed_without_replaying_command_or_unmount(self):
        self.prepare()
        self.remove_on_set = False
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'no confirmó el cierre'):
                self.finish()
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(len(self.fixture.commands), 1)
        self.removed = True
        self.assertTrue(self.finish()['verified'])

    def test_changed_session_or_fat_identity_blocks_terminal_after_unmount(self):
        self.prepare()
        def swap(*args, **kwargs):
            result = self.unmount(*args, **kwargs)
            self.current_status = replace(self.current_status, session='34'*16)
            return result
        self.system.run = swap
        with self.assertRaisesRegex(ValueError, 'PSP cambió'):
            self.finish()
        self.assertEqual(self.commands, [])
        self.current_status = status(native.SAFE, sequence=7, result=2)
        (self.fixture.dev / 'disk/by-uuid/ABCD-1234').rename(self.fixture.dev / 'disk/by-uuid/ABCD-5678')
        with self.assertRaisesRegex(ValueError, 'identidad FAT'):
            self.finish()
        self.assertEqual(self.commands, [])

    def test_durable_checkpoint_failure_before_send_prevents_usb_mutation(self):
        self.prepare()
        self.fail_stage = 'sending'
        with self.assertRaisesRegex(OSError, 'checkpoint failed'):
            self.finish()
        self.assertEqual(self.commands, [])
        self.assertEqual(len(self.fixture.commands), 1)
        self.fail_stage = None
        self.close = self.coordinator()
        self.assertTrue(self.finish()['verified'])
        self.assertEqual(len(self.fixture.commands), 1)

    def test_corrupt_receipt_or_foreign_path_fails_loading_before_open_or_unmount(self):
        self.prepare()
        original = json.loads(self.path.read_text())
        for mutate in (
                lambda record: record['observation'].update(path='/dev/hidraw999'),
                lambda record: record.update(stage='complete'),
                lambda record: record.update(command_hex='00'*65)):
            record = json.loads(json.dumps(original))
            mutate(record)
            self.path.write_text(json.dumps(record))
            before = list(self.events)
            with self.assertRaisesRegex(ValueError, 'registro de cierre necesita revisión'):
                self.coordinator()
            self.assertEqual(self.events, before)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.fixture.commands, [])

    def test_removal_requires_both_kernel_nodes_and_permission_error_is_not_absence(self):
        close = LinuxShutdown(None, system=self.system, api_factory=lambda: self.api)
        path = Path(self.fixture.path)
        path.touch()
        self.assertFalse(close.device_removed(str(self.fixture.usb), str(path)))
        path.unlink()
        self.assertFalse(close.device_removed(str(self.fixture.usb), str(path)))
        shutil.rmtree(self.fixture.usb)
        self.assertTrue(close.device_removed(str(self.fixture.usb), str(path)))
        with patch.object(Path, 'lstat', side_effect=PermissionError('no access')):
            with self.assertRaises(PermissionError):
                close.device_removed(str(self.fixture.usb), str(path))


if __name__ == '__main__':
    unittest.main()
