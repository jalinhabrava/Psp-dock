"""Host-only closure tests. No USB/device API or PSP filesystem is used."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

import native_dock as native
from native_shutdown import NativeShutdown, _atomic_json
from native_storage_release import (StorageReleaseError, verify_media_released,
    FSCTL_LOCK_VOLUME, FSCTL_DISMOUNT_VOLUME, IOCTL_STORAGE_EJECT_MEDIA)
from psp_usb import PspStorage


SESSION = '1234567890abcdef1234567890abcdef'
PATH = r'\\?\hid#vid_054c&pid_01cd&mi_01#7&cafe&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
PARENT = r'USB\VID_054C&PID_01CD\CARD'
GUID = '\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\'
STORAGE = PspStorage('G:\\', '12345', r'USBSTOR\DISK&VEN_SONY&PROD_PSP\CARD',
                     r'USB\VID_054C&PID_01CD&MI_00\CARD', 'Sony PSP USB Device')


class Hid:
    def __init__(self, owner):
        self.owner = owner
        self.status = native.NativeStatus(SESSION, native.STORAGE, 0, 0,
                                         True, False, True, False)
        self.commands = []
        self.on_set = lambda:None
        self.fail_get = False
        self.fail_set = False

    @contextmanager
    def open(self, path, write=False):
        if path != PATH:
            raise AssertionError('Unexpected HID path')
        self.owner.events.append(('open', write))
        yield 1

    def get(self, handle):
        self.owner.events.append(('get',))
        if self.fail_get:
            raise OSError('Synthetic read failure')
        return self.status

    def set(self, handle, payload):
        self.owner.events.append(('set',))
        self.commands.append(payload)
        self.on_set()
        if self.fail_set:
            raise OSError('Synthetic SET failure')


class NativeShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'native-close.json'
        self.events = []
        self.api = Hid(self)
        self.is_removed = False
        self.available = False
        self.media_error = 21
        self.release_count = 0
        self.release_failure = False
        self.now = 0.0
        self.parent_id = PARENT
        self.fail_stage = None
        self.close = self.coordinator()
        self.current = dict(session_id=SESSION, volume_serial=STORAGE.volume_serial,
            root=STORAGE.drive, path=PATH, usb_parent=PARENT,
            protocol_version=native.VERSION, firmware_revision=native.REVISION,
            capabilities=native.CAPABILITIES, build_id=native.BUILD_ID,
            sequence=0, phase=native.STORAGE, host_confirmed=False,
            storage_ready=True, controller_ready=False, safe_ready=False)

    def coordinator(self):
        return NativeShutdown(self.path, api_factory=lambda:self.api, release=self.release,
            verify_release=lambda receipt:verify_media_released(receipt, api=self),
            removed=lambda parent, path:self.is_removed, persist=self.persist,
            clock=lambda:self.now, pause=self.pause)

    def pause(self, delay):
        self.now += 0.5

    def persist(self, path, value):
        self.events.append(('persist', value['stage']))
        if value['stage'] == self.fail_stage:
            raise OSError('Synthetic durable checkpoint failure')
        _atomic_json(path, value)

    def receipt(self):
        return {'schema':'pspdock-native-storage-release-v1', 'identity':asdict(STORAGE),
                'usb_parent':PARENT, 'volume_guid':GUID, 'eject_kind':'media',
                'status':'media_ejected', 'host_eject_accepted':True, 'storage_released':True,
                'steps':[{'operation':op, 'code':code, 'ok':True} for op, code in (
                    ('lock_volume', FSCTL_LOCK_VOLUME), ('dismount_volume', FSCTL_DISMOUNT_VOLUME),
                    ('eject_media', IOCTL_STORAGE_EJECT_MEDIA))],
                'release_check':{'media_absent':True, 'available':False,
                                 'winerror':21, 'volume_guid':GUID}}

    def release(self, storage, parent, *, checkpoint):
        self.assertEqual(storage, STORAGE)
        self.assertEqual(parent, (42, PARENT))
        self.release_count += 1
        self.events.append(('release',))
        record = self.receipt()
        if self.release_failure:
            record.update(status='failed', host_eject_accepted=False,
                          storage_released=False, steps=[])
            raise StorageReleaseError('Synthetic lock veto', record)
        checkpoint(record)
        return record

    def media_status(self, guid):
        self.assertEqual(guid, GUID)
        self.events.append(('verify_media',))
        return {'available':self.available, 'winerror':self.media_error}

    def parent(self, instance):
        return 42, self.parent_id

    def prepare(self):
        self.close.prepare(self.current, STORAGE, cancel=not self.current['host_confirmed'])

    def finish(self):
        return self.close.finish(parent_of=self.parent)

    def remove_on_set(self):
        self.is_removed = True

    def test_unclaimed_close_releases_once_then_only_terminal_without_host_begin(self):
        self.prepare()
        self.api.on_set = self.remove_on_set
        result = self.finish()
        self.assertTrue(result['verified'])
        self.assertTrue(result['device_removed'])
        self.assertEqual(self.release_count, 1)
        self.assertEqual(len(self.api.commands), 1)
        self.assertEqual(self.api.commands[0], native.encode_command(SESSION, 1, native.SAFE, native.HOST_SHUTDOWN))
        self.assertLess(self.events.index(('persist', 'release_pending')), self.events.index(('release',)))
        self.assertLess(self.events.index(('verify_media',)), self.events.index(('set',)))
        self.assertLess(self.events.index(('persist', 'sending')), self.events.index(('set',)))

    def test_claimed_safe_close_preserves_sequence(self):
        self.current.update(phase=native.SAFE, host_confirmed=True, sequence=7)
        self.api.status = replace(self.api.status, phase=native.SAFE, host_confirmed=True,
                                  sequence=7, result=2, activity=native.PHASE_ACTIVITIES[native.SAFE][0])
        self.prepare()
        self.api.on_set = self.remove_on_set
        self.finish()
        self.assertEqual(self.api.commands[0], native.encode_command(SESSION, 8, native.SAFE, native.HOST_SHUTDOWN))

    def test_failed_release_sends_no_command_and_explicit_retry_can_release(self):
        self.prepare()
        self.release_failure = True
        with self.assertRaisesRegex(native.NativeDockError, 'lock veto'):
            self.finish()
        self.assertEqual(self.api.commands, [])
        self.assertEqual(self.close.record['stage'], 'release_blocked')
        self.release_failure = False
        self.api.on_set = self.remove_on_set
        self.assertTrue(self.finish()['verified'])

    def test_set_failure_is_success_only_when_actual_device_removal_is_observed(self):
        self.prepare()
        self.api.fail_set = True
        self.api.on_set = self.remove_on_set
        self.assertTrue(self.finish()['verified'])

    def test_failed_get_does_not_mean_removed_and_retry_does_not_release_again(self):
        self.prepare()
        self.api.on_set = lambda:setattr(self.api, 'fail_get', True)
        with self.assertRaisesRegex(native.NativeDockError, 'no confirmó'):
            self.finish()
        self.assertEqual(self.release_count, 1)
        self.api.fail_get = False
        self.api.on_set = self.remove_on_set
        result = self.finish()
        self.assertTrue(result['verified'])
        self.assertEqual(self.release_count, 1)
        self.assertEqual(self.api.commands[0], self.api.commands[1])

    def test_applied_shutdown_retry_only_observes_removal_without_duplicate_set(self):
        self.prepare()
        def applied():
            self.api.status = replace(self.api.status, phase=native.SAFE, sequence=1,
                                      result=2, shutdown_requested=True)
        self.api.on_set = applied
        with self.assertRaises(native.NativeDockError):
            self.finish()
        self.close = self.coordinator()
        with self.assertRaises(native.NativeDockError):
            self.finish()
        self.assertEqual(len(self.api.commands), 1)
        self.assertEqual(self.release_count, 1)
        self.is_removed = True
        self.assertTrue(self.finish()['verified'])

    def test_restart_recovers_same_session_without_storage_release_or_scan(self):
        self.prepare()
        with self.assertRaises(native.NativeDockError):
            self.finish()
        self.close = self.coordinator()
        seen = self.close.observation(probe=lambda:self.fail('must use retained HID'), parent_of=self.parent)
        self.assertTrue(seen['shutdown_pending'])
        self.assertFalse(seen['storage_ready'])
        self.assertFalse(seen['controller_ready'])
        self.api.on_set = self.remove_on_set
        self.finish()
        self.assertEqual(self.release_count, 1)

    def test_explicit_retry_of_consumed_failed_terminal_uses_next_sequence_without_eject(self):
        self.prepare()
        def rejected():
            self.api.status = replace(self.api.status, phase=native.ERROR, sequence=1,
                                      result=-3023, pending=False, shutdown_requested=False)
        self.api.on_set = rejected
        with self.assertRaisesRegex(native.NativeDockError, 'rechazó'):
            self.finish()
        self.assertEqual(len(self.api.commands), 1)
        self.close = self.coordinator()
        self.api.on_set = self.remove_on_set
        self.assertTrue(self.finish()['verified'])
        self.assertEqual(self.release_count, 1)
        self.assertEqual(self.api.commands[-1], native.encode_command(SESSION, 2, native.SAFE, native.HOST_SHUTDOWN))
        self.assertEqual(self.close.record['last_terminal_failure']['sequence'], 1)

    def test_identity_or_stale_sequence_is_rejected_before_any_volume_release(self):
        self.prepare()
        original = self.api.status
        for changed in (replace(original, session='ab'*16), replace(original, build_id=0x03040001),
                        replace(original, sequence=1), replace(original, pending=True),
                        replace(original, host_confirmed=True)):
            with self.subTest(status=changed):
                self.api.status = changed
                with self.assertRaises(native.NativeDockError):
                    self.finish()
                self.assertEqual(self.release_count, 0)
                self.assertEqual(self.api.commands, [])
        self.api.status = original
        self.parent_id = PARENT+'-OTHER'
        with self.assertRaises(native.NativeDockError):
            self.finish()
        self.assertEqual(self.release_count, 0)

    def test_reloaded_or_unknown_medium_prevents_terminal_command(self):
        self.prepare()
        for available, code in ((True, 0), (None, 5)):
            with self.subTest(available=available):
                self.available, self.media_error = available, code
                with self.assertRaisesRegex(native.NativeDockError, 'siga expulsada'):
                    self.finish()
                self.assertEqual(self.api.commands, [])
        self.assertEqual(self.release_count, 1)

    def test_intent_persistence_failure_prevents_volume_release(self):
        self.fail_stage = 'release_pending'
        with self.assertRaises(OSError):
            self.prepare()
        self.assertIsNone(self.close.record)
        self.assertEqual(self.release_count, 0)
        self.assertEqual(self.api.commands, [])

    def test_terminal_intent_persistence_failure_prevents_set_and_restart_retains_release(self):
        self.prepare()
        self.fail_stage = 'sending'
        with self.assertRaises(OSError):
            self.finish()
        self.assertEqual(self.api.commands, [])
        self.fail_stage = None
        self.close = self.coordinator()
        self.api.on_set = self.remove_on_set
        self.finish()
        self.assertEqual(self.release_count, 1)

    def test_uncertain_eject_is_not_repeated_or_converted_to_success(self):
        self.prepare()
        record = self.receipt()
        record.update(status='eject_requested', host_eject_accepted=False, storage_released=False, steps=[])
        self.close._save(release=record)
        with self.assertRaisesRegex(native.NativeDockError, 'sin confirmar'):
            self.finish()
        self.assertEqual(self.release_count, 0)
        self.assertEqual(self.api.commands, [])

    def test_corrupt_release_proof_cannot_turn_absence_into_completed_exit(self):
        self.prepare()
        record = deepcopy(self.close.record)
        record.update(stage='waiting_removal', release=self.receipt())
        mutations = [lambda r:r['release'].update(steps=[]),
                     lambda r:r['release']['release_check'].update(winerror=5),
                     lambda r:r['release']['release_check'].update(volume_guid='other'),
                     lambda r:r['release'].update(identity={**asdict(STORAGE), 'volume_serial':'999'})]
        self.is_removed = True
        for change in mutations:
            bad = deepcopy(record)
            change(bad)
            self.path.write_text(json.dumps(bad), encoding='utf-8')
            with self.subTest(record=bad), self.assertRaises(native.NativeDockError):
                self.coordinator()
        self.assertEqual(self.api.commands, [])

    def test_completed_receipt_finishes_crash_recovery_then_can_be_retired(self):
        self.prepare()
        self.api.on_set = self.remove_on_set
        self.finish()
        self.close = self.coordinator()
        self.assertTrue(self.close.pending)
        self.assertTrue(self.finish()['verified'])
        self.assertEqual(len(self.api.commands), 1)
        self.close.retire_completed()
        self.assertFalse(self.close.pending)


if __name__ == '__main__':
    unittest.main()
