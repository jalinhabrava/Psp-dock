import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psp_composite_watch as monitor


class CompositeWatchTests(unittest.TestCase):
    def test_failed_transfer_is_recorded_once_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'one-session'
            with patch.object(monitor, 'inspect', return_value={'devices': []}), \
                 patch.object(monitor, 'candidate_paths', return_value=['fixture']), \
                 patch.object(monitor.time, 'sleep'), \
                 patch.object(monitor, 'run_probe', side_effect=OSError('USB disconnected')) as run:
                with self.assertRaises(OSError):
                    monitor.watch(target, wait_seconds=1)
            self.assertEqual(run.call_count, 1)
            report = json.loads((target / 'watch.json').read_text())
            self.assertEqual(report['status'], 'interrupted')
            self.assertEqual(report['attempts'], 1)
            self.assertFalse(report['physical_usb_transfer_verified'])
            self.assertFalse(report['driver_changes'])

    def test_missing_winusb_preserves_diagnostics_without_sending_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'absent-interface'
            with patch.object(monitor, 'inspect', return_value={'winusb_present': False}), \
                 patch.object(monitor.time, 'monotonic', side_effect=[0, 0, 2]), \
                 patch.object(monitor, 'run_probe') as run:
                report = monitor.watch(target, wait_seconds=1)
            run.assert_not_called()
            self.assertEqual(report['status'], 'timed_out_waiting_for_winusb')
            self.assertFalse(report['physical_usb_transfer_verified'])
            self.assertTrue((target / 'after-pnp.json').is_file())


if __name__ == '__main__':
    unittest.main()
