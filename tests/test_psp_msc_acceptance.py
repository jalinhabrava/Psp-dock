import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import tools.psp.psp_msc_acceptance as msc
from psp_dock.devices.psp_composite_client import PROBE_SIZE
from psp_dock.devices.psp_winusb_transport import WinUSBError
from tests.test_psp_composite_acceptance import HID_PATH as BULK_HID_PATH, hid_report as bulk_hid_report
from tests.test_psp_composite_client import CONTENT, NONCE


HID_PATH = BULK_HID_PATH.replace('pid_01cb', 'pid_01cc')


def hid_report(session=NONCE):
    report = bulk_hid_report(session)
    report['devices'][0]['path'] = HID_PATH
    return report


PARENT = r'USB\VID_054C&PID_01CC\PSPMSC'
STORAGE = r'USB\VID_054C&PID_01CC&MI_01\STORAGE'
JOYSTICK = r'USB\VID_054C&PID_01CC&MI_00\JOYSTICK'
DISK = r'USBSTOR\DISK&VEN_PSPDOCK&PROD_MSC_HID_TEST&REV_0001\PSPMSC&0'
HID_NODE = '\\'.join(HID_PATH[4:].split('#')[:3])


def node(instance, parent, service):
    return {'instance': instance, 'parent': parent, 'status': 'OK',
            'service': service, 'hardware_ids': [r'USB\VID_054C&PID_01CC&REV_0207']}


def snapshot():
    composite = node(PARENT, 'USBROOT', 'usbccgp')
    return {'supported': True, 'backend': 'test', 'volumes': [{
        'root': 'Z:\\', 'label': 'PSPDOCKTEST', 'serial': '50444334',
        'filesystem': 'FAT', 'disk_pnp_id': DISK,
        'chain': [node(DISK, STORAGE, 'disk'), node(STORAGE, PARENT, 'USBSTOR'), composite],
    }], 'hid_nodes': [{'instance': HID_NODE, 'chain': [
        node(HID_NODE, JOYSTICK, 'HidUsb'), node(JOYSTICK, PARENT, 'HidUsb'), composite]}]}


class FakeFiles:
    hardware = False

    def __init__(self):
        self.data_reads = 0
        self.opened = []
        self.corrupt = False
        self.replace_session = False

    def chunks(self, path, size):
        self.opened.append(path.name)
        if path.name == 'SESSION.BIN':
            self.assert_size(size, 16)
            yield b'\x99' * 16 if self.replace_session and self.data_reads else NONCE
        elif path.name == 'PROBE.DAT':
            self.assert_size(size, PROBE_SIZE)
            for start in range(0, len(CONTENT), 65536):
                self.data_reads += 1
                chunk = CONTENT[start:start + 65536]
                yield b'X' + chunk[1:] if self.corrupt else chunk
        else:
            raise AssertionError('Unexpected file read')

    @staticmethod
    def assert_size(actual, expected):
        assert actual == expected


class MSCAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = FakeFiles()

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self):
        return {'connected': True, 'source': 'psp_winmm',
                'button_mask': self.files.data_reads % 2}

    def run_test(self, folder='probe', **overrides):
        arguments = {'rounds': 2, 'min_duration_seconds': 0,
                     'discover': snapshot, 'hid_probe': hid_report,
                     'read_input': self.sample, 'file_reader': self.files}
        arguments.update(overrides)
        return msc.run_probe(self.root / folder, **arguments)

    def test_correlates_hid_disk_parent_and_copies_only_synthetic_files(self):
        result = self.run_test()
        self.assertEqual(result['result'], 'verified')
        self.assertTrue(result['same_hid_storage_session'])
        self.assertTrue(result['synthetic_storage'])
        self.assertFalse(result['real_memory_stick_access'])
        self.assertFalse(result['physical_usb_transfer_verified'])
        self.assertFalse(result['simultaneous_hid_storage_verified'])
        self.assertFalse(result['native_usbstor_binding_verified'])
        self.assertEqual(result['completed_rounds'], 2)
        self.assertEqual(result['bytes_verified'], 2 * PROBE_SIZE)
        self.assertEqual((self.root / 'probe/probe-1.dat').read_bytes(), CONTENT)
        self.assertEqual(set(self.files.opened), {'SESSION.BIN', 'PROBE.DAT'})

    def test_unrelated_or_real_psp_volume_is_never_opened(self):
        changes = ({'label': 'MEMORYSTICK'}, {'serial': '00000000'},
                   {'disk_pnp_id': r'USBSTOR\DISK&VEN_SONY&PROD_PSP&REV_1\REAL'},
                   {'filesystem': 'NTFS'}, {'root': '\\\\?\\PhysicalDrive0'})
        for change in changes:
            data = snapshot()
            data['volumes'][0].update(change)
            with self.subTest(change=change), self.assertRaises(WinUSBError):
                self.run_test(discover=lambda: data)
        self.assertEqual(self.files.opened, [])

    def test_mismatched_parent_or_revision_cannot_pair_storage_with_hid(self):
        for kind in ('parent', 'revision', 'binding', 'chain'):
            data = snapshot()
            if kind == 'parent':
                hid = data['hid_nodes'][0]['chain']
                hid[1]['parent'] += 'OTHER'
                hid[2] = dict(hid[2], instance=PARENT + 'OTHER')
            elif kind == 'revision':
                data['volumes'][0]['chain'][2]['hardware_ids'] = [r'USB\VID_054C&PID_01CC&REV_0204']
            elif kind == 'binding':
                data['volumes'][0]['chain'][1]['service'] = 'WinUSB'
            else:
                data['volumes'][0]['chain'][0]['parent'] = 'unrelated'
            with self.subTest(kind=kind), self.assertRaises(WinUSBError):
                self.run_test(discover=lambda: data)
        self.assertEqual(self.files.opened, [])

    def test_hid_nonce_mismatch_reads_no_probe(self):
        with self.assertRaises(WinUSBError):
            self.run_test(hid_probe=lambda: hid_report(b'\x88' * 16))
        self.assertEqual(self.files.opened, ['SESSION.BIN'])
        record = json.loads((self.root / 'probe/evidence.json').read_text())
        self.assertEqual(record['result'], 'interrupted')

    def test_stable_hid_pid_cannot_be_accepted_as_msc_test(self):
        with self.assertRaises(WinUSBError):
            self.run_test(hid_probe=bulk_hid_report)
        self.assertEqual(self.files.opened, [])

    def test_changed_nonce_or_corruption_discards_partial(self):
        for fault in ('replace_session', 'corrupt'):
            self.files = FakeFiles()
            setattr(self.files, fault, True)
            with self.subTest(fault=fault), self.assertRaises(WinUSBError):
                self.run_test(folder=fault)
            self.assertEqual(list((self.root / fault).glob('*.dat')), [])
            self.assertEqual(list((self.root / fault).glob('*.part')), [])

    def test_no_motion_keeps_overall_acceptance_unverified(self):
        constant = lambda: {'connected': True, 'source': 'psp_winmm', 'button_mask': 0}
        result = self.run_test(read_input=constant)
        self.assertEqual(result['result'], 'motion_not_observed')
        self.assertFalse(result['simultaneous_hid_storage_verified'])

    def test_pacing_records_movement_during_copies_without_real_wait(self):
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        def sample():
            return {'connected': True, 'source': 'psp_winmm', 'button_mask': int(clock[0] > 0.1)}
        with patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(msc.time, 'sleep', side_effect=advance):
            result = self.run_test(min_duration_seconds=0.2, read_input=sample)
        self.assertEqual(result['result'], 'verified')
        self.assertGreaterEqual(result['seconds'], 0.2)
        self.assertGreaterEqual(result['gamepad_samples_during_transfer'], 10)

    def test_controller_loss_discards_partial_without_retry(self):
        def lost():
            return {'connected': not self.files.data_reads, 'source': 'psp_winmm', 'button_mask': 0}
        with self.assertRaises(WinUSBError):
            self.run_test(read_input=lost)
        self.assertEqual(self.files.data_reads, 1)
        self.assertEqual(list((self.root / 'probe').glob('*.dat')), [])
        self.assertEqual(list((self.root / 'probe').glob('*.part')), [])

    def test_ambiguous_volumes_fail_before_any_file_read(self):
        data = snapshot()
        data['volumes'] *= 2
        with self.assertRaises(WinUSBError):
            self.run_test(discover=lambda: data)
        self.assertEqual(self.files.opened, [])

    def test_pnp_discovery_does_not_change_drivers_or_issue_disk_commands(self):
        for forbidden in ('Set-PnpDevice', 'Disable-PnpDevice', 'Enable-PnpDevice',
                          'pnputil', 'Initialize-Disk', 'Format-Volume', 'Set-Disk'):
            self.assertNotIn(forbidden, msc.DISCOVER_SCRIPT)

    def test_monitor_timeout_preserves_initial_latest_final_pnp_without_probe(self):
        diagnostics = lambda: {'devices': [{'instance': 'USB\\VID_0000&PID_0002\\UNKNOWN'}]}
        acceptance = Mock()
        output = self.root / 'timeout'
        with self.assertRaises(WinUSBError):
            msc.monitor(output, wait_seconds=0, discover=lambda: {'volumes': []},
                        diagnostics=diagnostics, acceptance=acceptance)
        acceptance.assert_not_called()
        self.assertEqual({path.name for path in output.iterdir()},
                         {'before.json', 'latest.json', 'after.json', 'monitor.json', 'transitions.jsonl'})
        report = json.loads((output / 'monitor.json').read_text())
        self.assertEqual(report['result'], 'timed_out')
        self.assertEqual(report['probe_attempts'], 0)
        self.assertFalse(report['physical_usb_transfer_verified'])
        self.assertIn('VID_0000', (output / 'after.json').read_text())

    def test_monitor_keeps_discovery_errors_and_diagnostics_failure(self):
        def failing_discovery():
            raise OSError('Synthetic query failure')
        def failing_diagnostics():
            raise OSError('Synthetic diagnostic failure')
        output = self.root / 'discovery-error'
        with self.assertRaises(WinUSBError):
            msc.monitor(output, wait_seconds=0, discover=failing_discovery,
                        diagnostics=failing_diagnostics)
        latest = json.loads((output / 'latest.json').read_text())
        self.assertIn('Synthetic query failure', latest['observation']['discovery_error'])
        self.assertIn('Synthetic diagnostic failure', latest['diagnostic_error'])
        self.assertTrue((output / 'after.json').is_file())

    def test_monitor_invokes_probe_once_in_its_own_subdirectory(self):
        output = self.root / 'monitor'
        acceptance = Mock(return_value={'result': 'motion_not_observed',
                                       'physical_usb_transfer_verified': False})
        result = msc.monitor(output, discover=snapshot, diagnostics=lambda: {'devices': []},
                             acceptance=acceptance, rounds=8, min_duration_seconds=8)
        acceptance.assert_called_once_with(output / 'probe', rounds=8, min_duration_seconds=8)
        self.assertEqual(result['result'], 'motion_not_observed')
        self.assertEqual(result['probe_attempts'], 1)
        self.assertFalse(result['simultaneous_hid_storage_verified'])
        self.assertTrue((output / 'after.json').exists())

    def test_monitor_never_retries_a_failed_physical_probe(self):
        acceptance = Mock(side_effect=WinUSBError('Synthetic phase failure'))
        output = self.root / 'failed-probe'
        with self.assertRaises(WinUSBError):
            msc.monitor(output, wait_seconds=60, discover=snapshot,
                        diagnostics=lambda: {'devices': []}, acceptance=acceptance)
        acceptance.assert_called_once()
        result = json.loads((output / 'monitor.json').read_text())
        self.assertEqual(result['result'], 'interrupted')
        self.assertIn('Synthetic phase failure', result['error'])
        self.assertTrue((output / 'after.json').exists())

    def test_monitor_waits_for_hid_after_the_volume_mounts(self):
        waiting = snapshot()
        waiting['hid_nodes'] = []
        discovery = Mock(side_effect=[waiting, snapshot()])
        acceptance = Mock(return_value={'result': 'verified', 'physical_usb_transfer_verified': False})
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        with patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(msc.time, 'sleep', side_effect=advance):
            result = msc.monitor(self.root / 'hid-later', wait_seconds=5,
                                 discover=discovery, diagnostics=lambda: {'devices': []},
                                 acceptance=acceptance)
        self.assertEqual(discovery.call_count, 2)
        acceptance.assert_called_once()
        self.assertEqual(clock[0], 1)
        self.assertEqual(result['observations'], 2)
        self.assertEqual(result['probe_attempts'], 1)

    def test_monitor_hid_missing_times_out_without_opening_any_probe(self):
        waiting = snapshot()
        waiting['hid_nodes'] = []
        acceptance = Mock()
        output = self.root / 'hid-missing'
        with self.assertRaises(WinUSBError):
            msc.monitor(output, wait_seconds=0, discover=lambda: waiting,
                        diagnostics=lambda: {'devices': []}, acceptance=acceptance)
        acceptance.assert_not_called()
        record = json.loads((output / 'monitor.json').read_text())
        self.assertEqual(record['result'], 'timed_out')
        self.assertEqual(record['probe_attempts'], 0)
        self.assertIn('esperando a su interfaz HID', record['last_readiness']['reason'])
        self.assertFalse((output / 'probe').exists())

    def test_monitor_waits_until_hid_pnp_chain_is_operational(self):
        starting = snapshot()
        starting['hid_nodes'][0]['chain'][1]['status'] = 'Unknown'
        self.assertFalse(msc.pnp_readiness(starting)['ready'])
        self.assertTrue(msc.pnp_readiness(snapshot())['ready'])

    def test_monitor_ambiguous_candidates_fail_without_selecting_or_retrying(self):
        for field in ('volumes', 'hid_nodes'):
            ambiguous = snapshot()
            ambiguous[field] *= 2
            discovery, acceptance = Mock(return_value=ambiguous), Mock()
            with self.subTest(field=field), self.assertRaises(WinUSBError):
                msc.monitor(self.root / ('ambiguous-' + field), wait_seconds=30,
                            discover=discovery, diagnostics=lambda: {'devices': []},
                            acceptance=acceptance)
            discovery.assert_called_once()
            acceptance.assert_not_called()

    def test_monitor_wait_refreshes_diagnostics_and_remains_bounded(self):
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        diagnostics = Mock(return_value={'devices': []})
        output = self.root / 'paced-monitor'
        with patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(msc.time, 'sleep', side_effect=advance):
            with self.assertRaises(WinUSBError):
                msc.monitor(output, wait_seconds=16, discover=lambda: {'volumes': []},
                            diagnostics=diagnostics)
        self.assertEqual(clock[0], 16)
        self.assertEqual(diagnostics.call_count, 3)  # before, at 15 seconds, after
        result = json.loads((output / 'monitor.json').read_text())
        self.assertEqual(result['observations'], 17)

    def test_monitor_existing_output_is_never_overwritten(self):
        output = self.root / 'existing'
        output.mkdir()
        (output / 'monitor.json').write_text('ORIGINAL')
        discovery = Mock()
        with self.assertRaises(FileExistsError):
            msc.monitor(output, discover=discovery)
        discovery.assert_not_called()
        self.assertEqual((output / 'monitor.json').read_text(), 'ORIGINAL')

    def test_parent_and_storage_interface_history_survives_disappearance_before_mount(self):
        parent = node(PARENT, 'USBROOT', 'usbccgp')
        interface = node(STORAGE, PARENT, 'USBSTOR')
        first = {'volumes': [], 'hid_nodes': [], 'usb_nodes': [parent, interface]}
        reordered = dict(first, usb_nodes=[interface, parent])
        failing = copy.deepcopy(first)
        failing['usb_nodes'][1].update(status='Error', problem=10)
        gone = {'volumes': [], 'hid_nodes': [], 'usb_nodes': []}
        discovery = Mock(side_effect=[first, reordered, failing, gone])
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        output = self.root / 'transition-history'
        with patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(msc.time, 'sleep', side_effect=advance):
            with self.assertRaises(WinUSBError):
                msc.monitor(output, wait_seconds=3, discover=discovery,
                            diagnostics=lambda: {'devices': []})
        events = [json.loads(line) for line in (output / 'transitions.jsonl').read_text().splitlines()]
        changes = [event for event in events if event['kind'] == 'observation']
        self.assertEqual(len(changes), 3)
        self.assertEqual([event['elapsed_seconds'] for event in changes], [0, 2, 3])
        self.assertTrue(all(event['utc'].endswith('+00:00') for event in events))
        self.assertEqual({row['instance'] for row in changes[0]['state']['usb_nodes']}, {PARENT, STORAGE})
        self.assertTrue(any(row.get('problem') == 10 for row in changes[1]['state']['usb_nodes']))
        self.assertEqual(changes[2]['state']['usb_nodes'], [])
        self.assertEqual(json.loads((output / 'latest.json').read_text())['observation']['storage']['usb_nodes'], [])

    def test_discovery_error_is_kept_after_later_empty_successful_observation(self):
        discovery = Mock(side_effect=[OSError('Transient PnP error'), {'volumes': [], 'hid_nodes': [], 'usb_nodes': []}])
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        output = self.root / 'error-history'
        with patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(msc.time, 'sleep', side_effect=advance):
            with self.assertRaises(WinUSBError):
                msc.monitor(output, wait_seconds=1, discover=discovery,
                            diagnostics=lambda: {'devices': []})
        events = [json.loads(line) for line in (output / 'transitions.jsonl').read_text().splitlines()]
        self.assertTrue(any('Transient PnP error' in event['state'].get('discovery_error', '') for event in events))
        self.assertNotIn('discovery_error', json.loads((output / 'latest.json').read_text())['observation'])

    def test_history_limit_stops_before_probe_without_discarding_previous_entries(self):
        output = self.root / 'bounded-history'
        acceptance = Mock()
        with patch.object(msc.TransitionHistory, 'MAX_BYTES', 500):
            with self.assertRaises(WinUSBError):
                msc.monitor(output, discover=snapshot, diagnostics=lambda: {'devices': []}, acceptance=acceptance)
        acceptance.assert_not_called()
        self.assertLessEqual((output / 'transitions.jsonl').stat().st_size, 500)
        self.assertTrue(json.loads((output / 'monitor.json').read_text())['transition_history_limit_reached'])

    def test_previous_0206_firmware_stays_unready_for_0207_host(self):
        old = snapshot()
        old['volumes'][0]['chain'][2]['hardware_ids'] = [r'USB\VID_054C&PID_01CC&REV_0206']
        self.assertFalse(msc.pnp_readiness(old)['ready'])

    def test_native_discovery_distinguishes_each_missing_cim_association(self):
        base = snapshot()
        base.update(backend='windows-cim', usb_nodes=[])
        base['storage_inventory'] = {
            'queried': True, 'pnp_disk_count': 1, 'hid_pnp_count': 1,
            'disks': [], 'errors': [],
        }
        rows = (
            ([], [], 'pnp_disk_without_cim_disk'),
            ([{'pnp_id': DISK, 'index': 6, 'partitions': []}], [],
             'cim_disk_without_partition'),
            ([{'pnp_id': DISK, 'index': 6, 'partitions': [
                {'device_id': 'Disk #6, Partition #0', 'logical_disks': []}]}], [],
             'partition_without_logical_disk'),
            ([{'pnp_id': DISK, 'index': 6, 'partitions': [
                {'device_id': 'Disk #6, Partition #0', 'logical_disks': [{'device_id': 'Z:'}]}]}],
             snapshot()['volumes'], 'linked_logical_volume'),
        )
        for disks, volumes, expected in rows:
            data = copy.deepcopy(base)
            data['volumes'] = volumes
            data['storage_inventory']['disks'] = disks
            run = Mock(return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(data), stderr=''))
            with self.subTest(stage=expected), patch.object(msc.os, 'name', 'nt'):
                actual = msc.discover_volumes(run=run)
            self.assertEqual(actual['discovery_stage'], expected)
            self.assertEqual(actual['storage_inventory']['disks'], disks)
            self.assertEqual(actual['volumes'], volumes)
            self.assertTrue(run.call_args.args[0][-1].startswith('$includeGlobalVolumes=$false\n'))

    def test_matching_global_volume_remains_uncorrelated_and_never_starts_probe(self):
        data = snapshot()
        data['volumes'] = []
        data['storage_inventory'] = {
            'queried': True, 'pnp_disk_count': 1, 'hid_pnp_count': 1, 'errors': [],
            'disks': [{'pnp_id': DISK, 'index': 6, 'partitions': []}],
        }
        data['uncorrelated_volume_inventory'] = {
            'queried': True, 'parent_correlation': 'none', 'errors': [],
            'logical_disks': [{'device_id': 'Z:', 'label': msc.VOLUME_LABEL,
                               'serial': msc.VOLUME_SERIAL, 'filesystem': 'FAT'}],
            'volumes': [{'device_id': '\\\\?\\Volume{UNPAIRED}\\', 'drive_letter': 'Z:',
                         'label': msc.VOLUME_LABEL, 'serial': int(msc.VOLUME_SERIAL, 16)}],
        }
        output = self.root / 'global-only'
        acceptance = Mock()
        with self.assertRaises(WinUSBError):
            msc.monitor(output, wait_seconds=0, discover=lambda: data,
                        diagnostics=lambda: {'devices': []}, acceptance=acceptance)
        acceptance.assert_not_called()
        self.assertFalse((output / 'probe').exists())
        events = [json.loads(line) for line in (output / 'transitions.jsonl').read_text().splitlines()]
        event = next(item for item in events if item['kind'] == 'observation')
        self.assertEqual(event['state']['volumes'], [])
        self.assertEqual(event['state']['storage_inventory'], data['storage_inventory'])
        self.assertEqual(event['state']['uncorrelated_volume_inventory'],
                         data['uncorrelated_volume_inventory'])
        self.assertEqual(event['state']['readiness']['discovery_stage'], 'cim_disk_without_partition')
        self.assertIn('observed_utc', event)
        self.assertIn('completed_utc', event)

    def test_partial_cim_query_failure_blocks_an_otherwise_valid_volume(self):
        data = snapshot()
        data['storage_inventory'] = {
            'queried': True, 'pnp_disk_count': 1, 'disks': [],
            'errors': [{'stage': 'partitions', 'instance': DISK, 'error': 'Provider timeout'}],
        }
        self.assertEqual(msc.discovery_stage(data), 'cim_query_error')
        self.assertFalse(msc.pnp_readiness(data)['ready'])
        with self.assertRaisesRegex(WinUSBError, 'consulta CIM'):
            self.run_test(discover=lambda: data)
        self.assertEqual(self.files.opened, [])

    def test_cim_query_error_stage_survives_a_later_empty_snapshot(self):
        failed = {'volumes': [], 'usb_nodes': [], 'hid_nodes': [],
                  'storage_inventory': {'queried': True, 'pnp_disk_count': 1, 'disks': [],
                                        'errors': [{'stage': 'logical_disks',
                                                    'instance': 'Disk #6, Partition #0',
                                                    'error': 'CIM transient failure'}]}}
        discovery = Mock(side_effect=[failed, {'volumes': [], 'usb_nodes': [], 'hid_nodes': []}])
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        output = self.root / 'cim-error-history'
        with patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(msc.time, 'sleep', side_effect=advance), self.assertRaises(WinUSBError):
            msc.monitor(output, wait_seconds=1, discover=discovery,
                        diagnostics=lambda: {'devices': []})
        events = [json.loads(line) for line in (output / 'transitions.jsonl').read_text().splitlines()]
        error = next(event['state']['storage_inventory']['errors'][0] for event in events
                     if event['kind'] == 'observation' and 'storage_inventory' in event['state'])
        self.assertEqual(error['stage'], 'logical_disks')
        self.assertEqual(error['error'], 'CIM transient failure')
        latest = json.loads((output / 'latest.json').read_text())
        self.assertNotIn('storage_inventory', latest['observation']['storage'])

    def test_global_inventory_is_rate_limited_even_when_provider_fails(self):
        clock, scanned = [0.0], []
        def advance(seconds):
            clock[0] += seconds
        def discover(*, include_global_volumes):
            if include_global_volumes:
                scanned.append(clock[0])
            return {'volumes': [], 'usb_nodes': [], 'hid_nodes': [],
                    'uncorrelated_volume_inventory': {
                        'queried': include_global_volumes, 'parent_correlation': 'none',
                        'errors': [{'stage': 'global_volumes', 'error': 'Provider unavailable'}]
                                  if include_global_volumes else [],
                    }}
        acceptance = Mock()
        with patch.object(msc, 'discover_volumes', discover), \
             patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(msc.time, 'sleep', side_effect=advance), self.assertRaises(WinUSBError):
            msc.monitor(self.root / 'global-cadence', wait_seconds=16, discover=discover,
                        diagnostics=lambda: {'devices': []}, acceptance=acceptance)
        self.assertEqual(scanned, [0, 15])
        acceptance.assert_not_called()

    def test_observation_is_journaled_before_slow_diagnostics(self):
        clock, captures = [0.0], [0]
        def discover():
            clock[0] = 16
            return {'volumes': [], 'usb_nodes': [], 'hid_nodes': []}
        def diagnostics():
            captures[0] += 1
            if captures[0] > 1:
                clock[0] += 5
            return {'devices': [{'instance': 'observation-' + str(captures[0])}]}
        output = self.root / 'capture-timing'
        with patch.object(msc.time, 'monotonic', side_effect=lambda: clock[0]), \
             self.assertRaises(WinUSBError):
            msc.monitor(output, wait_seconds=0, discover=discover, diagnostics=diagnostics)
        events = [json.loads(line) for line in (output / 'transitions.jsonl').read_text().splitlines()]
        observed = next(event for event in events if event['kind'] == 'observation')
        delayed = next(event for event in events if event['kind'] == 'diagnostics'
                       and event['state']['devices'][0]['instance'] == 'observation-2')
        self.assertEqual(observed['elapsed_seconds'], 16)
        self.assertEqual(delayed['elapsed_seconds'], 21)
        self.assertLess(observed['sequence'], delayed['sequence'])


if __name__ == '__main__':
    unittest.main()
