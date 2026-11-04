from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psp_dock.devices.psp_composite_diagnostics as diagnostic


class CompositeDiagnosticTests(unittest.TestCase):
    def test_no_installer_or_reset_in_pnp_query(self):
        for word in ('Set-PnpDevice', 'Disable-PnpDevice', 'Enable-PnpDevice',
                     'pnputil', 'reg add', 'Set-ItemProperty', 'Remove-Item'):
            self.assertNotIn(word, diagnostic.PNP_SCRIPT)
        self.assertIn('Get-PnpDevice -PresentOnly', diagnostic.PNP_SCRIPT)

    def test_nonwindows_is_read_only_noop(self):
        with patch.object(diagnostic.os, 'name', 'posix'):
            self.assertFalse(diagnostic.windows_binding_snapshot()['supported'])

    def test_missing_bulk_does_not_try_to_bind_a_driver(self):
        with patch.object(diagnostic, 'windows_binding_snapshot', return_value={'devices': []}), \
             patch.object(diagnostic, 'candidate_paths', return_value=[]), \
             patch('psp_dock.devices.psp_winusb_transport.WinUSBTransport') as opener:
            report = diagnostic.inspect(firmware=True)
        opener.assert_not_called()
        self.assertFalse(report['physical_transfer_verified'])
        self.assertFalse(report['file_commands_sent'])
        self.assertIn('firmware_error', report)

    def test_snapshot_preserves_unknown_usb_failure_and_parent_location(self):
        rows = [{'instance': r'USB\VID_0000&PID_0002\123',
                 'unidentified_usb_failure': True, 'psp_identity_confirmed': False,
                 'properties': {'DEVPKEY_Device_Parent': 'USBHUB',
                                'DEVPKEY_Device_LocationPaths': ['USBROOT(0)#USB(4)']}}]
        with patch.object(diagnostic.os, 'name', 'nt'):
            result = diagnostic.windows_binding_snapshot(run=lambda *args, **kwargs:
                SimpleNamespace(returncode=0, stdout='\ufeff'+json.dumps(rows), stderr=''))
        self.assertEqual(result['devices'], rows)
        self.assertFalse(result['winusb_present'])
        self.assertIn('VID_0000&PID_000[02]', diagnostic.PNP_SCRIPT)

    def test_setupapi_retains_only_relevant_bounded_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'setupapi.dev.log'
            path.write_text('>>> [Other device - PCI\\PRIVATE]\nprivate unrelated text\n' +
                            '>>> [Device install - USB\\VID_054C&PID_01CB]\n! driver bind\n' +
                            '>>> [Other device - USB\\VID_1234&PID_5678]\nnot psp\n' +
                            '>>> [Device install - USB\\VID_0000&PID_0002]\n! descriptor failed\n',
                            encoding='utf-8')
            result = diagnostic.setupapi_snapshot(path)
        self.assertEqual(len(result['sections']), 2)
        self.assertTrue(result['unidentified_usb_entries_are_not_psp_proof'])
        self.assertNotIn('private unrelated text', ''.join(result['sections']))
        self.assertNotIn('not psp', ''.join(result['sections']))

    def test_setupapi_missing_log_does_not_break_pnp_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            result = diagnostic.setupapi_snapshot(Path(directory) / 'missing.log')
        self.assertEqual(result['sections'], [])
        self.assertIn('error', result)

    def test_setupapi_body_mentions_do_not_capture_unrelated_driver_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'setupapi.dev.log'
            unrelated = '>>> [SetupCopyOEMInf - OTHER-DRIVER]\n' + ('private unrelated line\n' * 3000)
            unrelated += 'Hardware IDs supported: USB\\VID_0000&PID_0002\n'
            relevant = '>>> [Device Install (Hardware initiated) - USB\\VID_054C&PID_01CC\\TEST]\n'
            path.write_text(unrelated + relevant + ('actual device details\n' * 1000), encoding='utf-8')
            result = diagnostic.setupapi_snapshot(path)
        self.assertEqual(len(result['sections']), 1)
        self.assertNotIn('OTHER-DRIVER', result['sections'][0])
        self.assertNotIn('private unrelated line', result['sections'][0])
        self.assertLessEqual(len(result['sections'][0]), 4000)
        self.assertIn('PID_01CC', result['sections'][0])


if __name__ == '__main__':
    unittest.main()
