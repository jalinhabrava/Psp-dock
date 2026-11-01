"""Combined HID + bulk evidence cannot be fabricated by simulated tests."""
import json
from pathlib import Path
import tempfile
import unittest

from psp_composite_acceptance import run_probe, matching_hid_session
from psp_winusb_transport import WinUSBTransport, WinUSBError
from tests.test_psp_composite_client import FakeUSB, PATH, NONCE


HID_PATH = r'\\?\hid#vid_054c&pid_01cb&mi_00#7&test&0&0000#{hid-guid}'


def hid_report(session=NONCE):
    return {'supported': True, 'errors': [], 'devices': [{
        'path': HID_PATH, 'status': {
            'session': session.hex(), 'connected': True, 'version': 1,
            'mode': 2, 'pending': False, 'consumed': False,
            'sequence': 0, 'result': 0,
        }
    }]}


class CombinedAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fake = FakeUSB()

    def tearDown(self):
        self.tmp.cleanup()

    def opener(self, path):
        return WinUSBTransport(path, backend=self.fake, enumerate_paths=lambda: [PATH])

    def sample(self):
        return {'connected': True, 'source': 'psp_winmm',
                'button_mask': self.fake.count % 2}

    def test_matching_session_transfers_only_probe_and_marks_simulation(self):
        dest = self.root / 'evidence'
        report = run_probe(dest, rounds=2, enumerate_bulk=lambda: [PATH],
                           open_transport=self.opener, hid_probe=hid_report,
                           read_input=self.sample, storage_present=lambda: False)
        self.assertEqual(report['result'], 'verified')
        self.assertEqual(report['completed_rounds'], 2)
        self.assertEqual(report['bytes_verified'], 300006)
        self.assertTrue(report['same_hid_bulk_session'])
        self.assertTrue(report['gamepad_motion_observed'])
        self.assertFalse(report['physical_usb_transfer_verified'])
        self.assertFalse(report['save_return_supported'])
        self.assertFalse(report['fat_volume_identity_verified'])
        self.assertEqual(json.loads((dest / 'evidence.json').read_text())['usb_backend'], 'test')
        self.assertEqual(self.fake.closed, 1)

    def test_foreign_hid_session_never_sends_bulk_commands(self):
        with self.assertRaises(WinUSBError):
            run_probe(self.root / 'mismatch', enumerate_bulk=lambda: [PATH],
                      open_transport=self.opener,
                      hid_probe=lambda: hid_report(b'\x77' * 16),
                      read_input=self.sample, storage_present=lambda: False)
        self.assertEqual(self.fake.writes, [])
        record = json.loads((self.root / 'mismatch/evidence.json').read_text())
        self.assertEqual(record['result'], 'interrupted')
        self.assertFalse(record['physical_usb_transfer_verified'])

    def test_storage_present_blocks_device_open(self):
        with self.assertRaises(WinUSBError):
            run_probe(self.root / 'no-writes', enumerate_bulk=lambda: [PATH],
                      open_transport=self.opener, hid_probe=hid_report,
                      read_input=self.sample, storage_present=lambda: True)
        self.assertFalse(self.fake.opened)
        self.assertFalse((self.root / 'no-writes').exists())

    def test_noncomposite_or_ambiguous_hid_is_not_accepted(self):
        for variant in ('legacy', 'multiple', 'pending'):
            report = hid_report()
            if variant == 'legacy':
                report['devices'][0]['path'] = HID_PATH.replace('&mi_00', '')
            elif variant == 'multiple':
                report['devices'] *= 2
            else:
                report['devices'][0]['status']['pending'] = True
            with self.subTest(variant=variant), self.assertRaises(WinUSBError):
                matching_hid_session(report, NONCE)

    def test_controller_loss_aborts_probe_without_automatic_retry(self):
        reads = 0
        def sample():
            nonlocal reads
            reads += 1
            return {'connected': reads == 1, 'source': 'psp_winmm', 'button_mask': 0}
        with self.assertRaises(WinUSBError):
            run_probe(self.root / 'controller-lost', enumerate_bulk=lambda: [PATH],
                      open_transport=self.opener, hid_probe=hid_report,
                      read_input=sample, storage_present=lambda: False)
        output = self.root / 'controller-lost'
        self.assertEqual(list(output.glob('*.dat')), [])
        self.assertEqual(list(output.glob('*.part')), [])
        self.assertEqual(json.loads((output / 'evidence.json').read_text())['result'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
