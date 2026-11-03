"""Host-only release ordering and identity tests; never touch real USB."""
from copy import deepcopy
from dataclasses import replace
import ctypes
import unittest

import native_storage_release as release
from psp_usb import PspStorage


IDENTITY = PspStorage('G:\\', '12345', 'USBSTOR\\DISK&VEN_SONY&PROD_PSP\\CARD',
                      'USB\\VID_054C&PID_01CD&MI_00\\CARD', 'Sony PSP USB Device')
PARENT = (42, 'USB\\VID_054C&PID_01CD\\CARD')
GUID = '\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\'


class IoFailure(OSError):
    def __init__(self, code):
        super().__init__('Synthetic Win32 error ' + str(code))
        self.winerror = code


class FakeApi:
    def __init__(self):
        self.handle = object()
        self.calls = []
        self.failure = {}
        self.guid = GUID
        self.serial = IDENTITY.volume_serial
        self.number = {'device_type':7, 'device_number':6, 'partition_number':1}
        self.number_after_lock = None
        self.after_guid = None
        self.guid_reads = 0
        self.number_reads = 0
        self.locked = False
        self.closed = False
        self.media = {'available':False, 'winerror':21}
        self.topology_value = {
            'disk_id':IDENTITY.disk_id, 'disk_number':6, 'disk_device':r'\\.\PHYSICALDRIVE6',
            'partition_count':1, 'selected_partition':'Disk #6, Partition #0',
            'partitions':['Disk #6, Partition #0'], 'logical_volumes':['G:']}

    def topology(self, drive):
        self.calls.append(('topology', drive))
        return deepcopy(self.topology_value)

    def volume_guid(self, drive):
        self.guid_reads += 1
        self.calls.append(('volume_guid', drive))
        return self.after_guid if self.guid_reads > 1 and self.after_guid else self.guid

    def volume_serial(self, guid):
        self.calls.append(('serial', guid))
        return self.serial

    def open_volume(self, guid):
        self.calls.append(('open', guid))
        return self.handle

    def device_number(self, handle):
        self.calls.append(('number', handle))
        self.number_reads += 1
        return deepcopy(self.number_after_lock if self.locked and self.number_after_lock else self.number)

    def control(self, handle, code):
        self.calls.append(('control', handle, code))
        if code in self.failure:
            raise IoFailure(self.failure[code])
        if code == release.FSCTL_LOCK_VOLUME:
            self.locked = True
        elif code == release.FSCTL_UNLOCK_VOLUME:
            self.locked = False

    def close(self, handle):
        self.calls.append(('close', handle))
        self.closed = True
        self.locked = False

    def media_status(self, guid):
        assert self.closed and not self.locked, 'post-eject verification must follow handle cleanup'
        self.calls.append(('media_status', guid))
        return deepcopy(self.media)


class NativeStorageReleaseTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.records = []
        self.identify = lambda drive:(IDENTITY, 40)
        self.parent = lambda instance:PARENT

    def save(self, record):
        self.records.append(deepcopy(record))
        self.api.calls.append(('checkpoint', record['status']))

    def run_release(self, checkpoint=None):
        return release.release_native_storage(IDENTITY, PARENT,
            checkpoint=checkpoint or self.save, api=self.api,
            identify_storage=self.identify, parent_of=self.parent)

    def controls(self):
        return [row[2] for row in self.api.calls if row[0] == 'control']

    def assert_not_ejected(self, error):
        self.assertFalse(error.storage_released)
        self.assertFalse(error.receipt['host_eject_accepted'])

    def test_one_stable_handle_and_durable_intent_precede_normal_media_eject(self):
        result = self.run_release()
        self.assertTrue(result['storage_released'])
        self.assertTrue(result['host_eject_accepted'])
        self.assertEqual(result['eject_kind'], 'media')
        self.assertTrue(result['cleanup_complete'])
        self.assertTrue(result['release_check']['media_absent'])
        self.assertEqual(result['release_check']['winerror'], 21)
        self.assertEqual(self.controls(), [release.FSCTL_LOCK_VOLUME, release.FSCTL_DISMOUNT_VOLUME,
            release.IOCTL_STORAGE_EJECT_MEDIA, release.FSCTL_UNLOCK_VOLUME])
        for row in self.api.calls:
            if row[0] in ('control', 'close', 'number'):
                self.assertIs(row[1], self.api.handle)
        self.assertEqual([r for r in self.api.calls if r[0] == 'open'], [('open', GUID)])
        intent = self.api.calls.index(('checkpoint', 'release_requested'))
        lock = self.api.calls.index(('control', self.api.handle, release.FSCTL_LOCK_VOLUME))
        self.assertLess(intent, lock)
        eject_intent = self.api.calls.index(('checkpoint', 'eject_requested'))
        eject = self.api.calls.index(('control', self.api.handle, release.IOCTL_STORAGE_EJECT_MEDIA))
        self.assertLess(eject_intent, eject)
        self.assertEqual(next(r for r in self.records if r['status'] == 'eject_requested')['pending_operation'],
                         'eject_media')
        self.assertEqual(self.records[-1], result)
        self.assertEqual(ctypes.sizeof(release._StorageDeviceNumber), 12)

    def test_changed_psp_or_parent_cannot_open_or_dismount_a_volume(self):
        for changed in ('card', 'parent'):
            with self.subTest(changed=changed):
                self.setUp()
                if changed == 'card':
                    self.identify = lambda drive:(replace(IDENTITY, volume_serial='999'), 40)
                else:
                    self.parent = lambda instance:(43, 'USB\\VID_054C&PID_01CD\\OTHER')
                with self.assertRaises(release.StorageReleaseError) as ctx:
                    self.run_release()
                self.assert_not_ejected(ctx.exception)
                self.assertFalse(any(r[0] == 'open' for r in self.api.calls))
                self.assertEqual(self.controls(), [])

    def test_wrong_disk_or_more_than_one_partition_or_volume_is_rejected(self):
        cases = [
            {'disk_id':'USBSTOR\\ANOTHER'}, {'disk_device':r'\\.\PHYSICALDRIVE7'},
            {'disk_number':True}, {'partition_count':2},
            {'partitions':['Disk #6, Partition #0', 'Disk #6, Partition #1']},
            {'logical_volumes':['G:', 'H:']}, {'logical_volumes':['H:']}]
        for change in cases:
            with self.subTest(change=change):
                self.setUp()
                self.api.topology_value.update(change)
                with self.assertRaises(release.StorageReleaseError) as ctx:
                    self.run_release()
                self.assert_not_ejected(ctx.exception)
                self.assertFalse(any(r[0] == 'open' for r in self.api.calls))
                self.assertEqual(self.controls(), [])

    def test_opened_handle_must_match_the_physical_disk_and_a_partition(self):
        for change in ({'device_number':7}, {'device_type':2}, {'partition_number':0},
                       {'partition_number':0xFFFFFFFF}):
            with self.subTest(change=change):
                self.setUp()
                self.api.number.update(change)
                with self.assertRaises(release.StorageReleaseError) as ctx:
                    self.run_release()
                self.assert_not_ejected(ctx.exception)
                self.assertTrue(self.api.closed)
                self.assertEqual(self.controls(), [])

    def test_letter_reassignment_after_open_cannot_eject_the_opened_volume(self):
        self.api.after_guid = GUID.replace('12345678', 'aaaaaaaa')
        with self.assertRaises(release.StorageReleaseError) as ctx:
            self.run_release()
        self.assert_not_ejected(ctx.exception)
        self.assertEqual(self.controls(), [])
        self.assertTrue(self.api.closed)

    def test_wrong_volume_serial_is_rejected_before_open(self):
        self.api.serial = '888'
        with self.assertRaises(release.StorageReleaseError):
            self.run_release()
        self.assertFalse(any(r[0] == 'open' for r in self.api.calls))

    def test_failure_to_persist_intent_performs_no_mutating_ioctl(self):
        def fail(record):
            raise OSError('Journal unavailable')
        with self.assertRaises(release.StorageReleaseError) as ctx:
            self.run_release(fail)
        self.assert_not_ejected(ctx.exception)
        self.assertEqual(self.controls(), [])
        self.assertTrue(self.api.closed)

    def test_lock_failure_never_dismounts_or_ejects(self):
        self.api.failure[release.FSCTL_LOCK_VOLUME] = 5
        with self.assertRaises(release.StorageReleaseError) as ctx:
            self.run_release()
        self.assert_not_ejected(ctx.exception)
        self.assertEqual(self.controls(), [release.FSCTL_LOCK_VOLUME])
        self.assertEqual(ctx.exception.receipt['error']['winerror'], 5)
        self.assertTrue(self.api.closed)

    def test_identity_change_during_lock_unlocks_without_dismount(self):
        self.api.number_after_lock = {**self.api.number, 'device_number':7}
        with self.assertRaises(release.StorageReleaseError) as ctx:
            self.run_release()
        self.assert_not_ejected(ctx.exception)
        self.assertEqual(self.controls(), [release.FSCTL_LOCK_VOLUME, release.FSCTL_UNLOCK_VOLUME])
        self.assertTrue(self.api.closed)

    def test_dismount_failure_never_ejects(self):
        self.api.failure[release.FSCTL_DISMOUNT_VOLUME] = 32
        with self.assertRaises(release.StorageReleaseError) as ctx:
            self.run_release()
        self.assert_not_ejected(ctx.exception)
        self.assertEqual(self.controls(), [release.FSCTL_LOCK_VOLUME, release.FSCTL_DISMOUNT_VOLUME,
                                           release.FSCTL_UNLOCK_VOLUME])
        self.assertTrue(self.api.closed)

    def test_unsupported_eject_is_not_release_proof_and_does_not_try_an_alternative(self):
        self.api.failure[release.IOCTL_STORAGE_EJECT_MEDIA] = 1
        with self.assertRaises(release.StorageReleaseError) as ctx:
            self.run_release()
        self.assert_not_ejected(ctx.exception)
        self.assertEqual(self.controls(), [release.FSCTL_LOCK_VOLUME, release.FSCTL_DISMOUNT_VOLUME,
            release.IOCTL_STORAGE_EJECT_MEDIA, release.FSCTL_UNLOCK_VOLUME])
        self.assertFalse(any(r[0] == 'media_status' for r in self.api.calls))

    def test_eject_acceptance_without_verified_media_absence_is_not_release(self):
        for check in ({'available':True, 'winerror':0}, {'available':None, 'winerror':5},
                      {'available':False, 'winerror':3}):
            with self.subTest(check=check):
                self.setUp()
                self.api.media = check
                with self.assertRaises(release.StorageReleaseError) as ctx:
                    self.run_release()
                self.assertFalse(ctx.exception.storage_released)
                self.assertTrue(ctx.exception.receipt['host_eject_accepted'])
                self.assertTrue(self.api.closed)

    def test_no_media_error_is_valid_only_after_successful_eject(self):
        self.api.media = {'available':False, 'winerror':1112}
        self.assertTrue(self.run_release()['storage_released'])

    def test_post_eject_journal_failure_preserves_actual_release_for_reconciliation(self):
        def fail_after_eject(record):
            if record['host_eject_accepted']:
                raise OSError('Journal failed after actual eject')
            self.save(record)
        with self.assertRaises(release.StorageReleaseError) as ctx:
            self.run_release(fail_after_eject)
        self.assertTrue(ctx.exception.storage_released)
        self.assertTrue(ctx.exception.receipt['host_eject_accepted'])
        self.assertEqual(ctx.exception.receipt['release_check']['winerror'], 21)
        self.assertEqual(self.controls().count(release.IOCTL_STORAGE_EJECT_MEDIA), 1)
        self.assertTrue(self.api.closed)

    def test_unlock_error_does_not_erase_verified_eject_and_handle_still_closes(self):
        self.api.failure[release.FSCTL_UNLOCK_VOLUME] = 21
        result = self.run_release()
        self.assertTrue(result['storage_released'])
        self.assertFalse(result['cleanup_complete'])
        self.assertTrue(self.api.closed)

    def test_retry_verification_is_read_only_and_never_repeats_eject(self):
        receipt = self.run_release()
        self.api.calls.clear()
        result = release.verify_media_released(receipt, api=self.api)
        self.assertTrue(result['media_absent'])
        self.assertEqual(self.api.calls, [('media_status', GUID)])

    def test_uncertain_or_failed_eject_cannot_be_upgraded_to_success_by_absence(self):
        confirmed = self.run_release()
        self.api.calls.clear()
        for receipt in ({**confirmed, 'host_eject_accepted':False},
                        {**confirmed, 'steps':[]},
                        {**confirmed, 'steps':[{'operation':'eject_media',
                            'code':release.IOCTL_STORAGE_EJECT_MEDIA, 'ok':False}]},
                        {**confirmed, 'volume_guid':'G:\\'}):
            with self.subTest(receipt=receipt):
                with self.assertRaises(release.UsbTransitionError):
                    release.verify_media_released(receipt, api=self.api)
        self.assertEqual(self.api.calls, [])


if __name__ == '__main__':
    unittest.main()
