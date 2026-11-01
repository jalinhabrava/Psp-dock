from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psp_composite_diagnostics as diagnostic


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
             patch('psp_winusb_transport.WinUSBTransport') as opener:
            report = diagnostic.inspect(firmware=True)
        opener.assert_not_called()
        self.assertFalse(report['physical_transfer_verified'])
        self.assertFalse(report['file_commands_sent'])
        self.assertIn('firmware_error', report)


if __name__ == '__main__':
    unittest.main()
