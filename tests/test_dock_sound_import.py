"""Verify automatic import admission with the existing fake native session."""
from unittest.mock import patch
import unittest

from tests import test_dock_product_session as fixtures
from psp_dock.core.dock_product_flow import ProductFlowError
from psp_dock.media.xmb_sound_import import import_from_psp


class AutomaticSoundImportTests(unittest.TestCase):
    setUp = fixtures.DockProductSessionTests.setUp
    tick = fixtures.DockProductSessionTests.tick
    offer = fixtures.DockProductSessionTests.offer

    def test_import_only_after_host_begin_with_verified_volume_and_before_offer(self):
        calls = []
        def import_sounds(root, cache, *, device, verify_device):
            self.assertTrue(self.transport.host_confirmed)
            self.assertEqual(self.transport.phase, 1)
            self.assertEqual(root, self.psp)
            self.assertEqual(cache, self.dock.data / 'xmb-ui-sounds')
            self.assertEqual(device['volume_serial'], self.transport.serial)
            self.assertEqual(verify_device(), self.psp)
            calls.append('import')
            return {'status': 'imported', 'ready': True}
        with patch('psp_dock.core.dock_product_session.import_from_psp', side_effect=import_sounds) as imported:
            for _ in range(3):
                self.product.snapshot()
            imported.assert_not_called()
            self.offer()
            self.product.snapshot()
            self.assertEqual(calls, ['import'])
            self.assertEqual(self.product.snapshot()['ui_sounds'], {'status':'imported','ready':True})

    def test_unsupported_optional_audio_does_not_block_inventory(self):
        with patch('psp_dock.core.dock_product_session.import_from_psp',
                   return_value={'status':'unsupported','ready':False}) as imported:
            self.offer()
            self.assertEqual(self.product.snapshot()['phase'], 'choosing')
            self.assertFalse(self.product.snapshot()['ui_sounds']['ready'])
            imported.assert_called_once()

    def test_valid_cache_adds_no_storage_rebind_for_audio(self):
        def cached(root, cache, **kwargs):
            self.assertEqual(checked.call_count, 0)
            result = import_from_psp(root, cache, **kwargs)
            self.assertEqual(checked.call_count, 0)
            return result
        with patch.object(self.product, '_context_root', wraps=self.product._context_root) as checked, \
                patch('psp_dock.media.xmb_sound_import.cache_status', return_value={'status':'cached','ready':True}), \
                patch('psp_dock.core.dock_product_session.import_from_psp', side_effect=cached):
            self.offer()
        self.assertEqual(self.product.snapshot()['ui_sounds']['status'], 'cached')

    def test_device_guard_failure_is_not_treated_as_missing_optional_audio(self):
        with patch('psp_dock.core.dock_product_session.import_from_psp', side_effect=ProductFlowError('Device changed')):
            self.tick()
        self.assertEqual(self.product.snapshot()['phase'], 'attention')
        self.assertFalse(self.product.snapshot()['can_choose'])


if __name__ == '__main__':
    unittest.main()
