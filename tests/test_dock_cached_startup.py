"""Instant offline catalog is visually useful but never grants game access."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock


class CatalogOnStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.data=Path(self.temp.name)/'data'
        self.data.mkdir()
        (self.data/'inventory-cache.json').write_text(json.dumps({
            'volume_serial':'2958455453',
            'games':[{
                'id':'a'*24,'relative':'ISO/Example.iso',
                'title':'Example','format':'ISO','size':1234567,
                'modified_ns':1234,
            }],
        }),encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_shows_old_catalog_before_heavy_usb_audit_but_is_not_ready(self):
        dock=Dock(self.data)
        self.assertTrue(dock.snapshot['scanning'])
        self.assertEqual(dock.snapshot['library_source'],'cached_manifest')
        self.assertEqual(len(dock.snapshot['games']),1)
        self.assertEqual(dock.snapshot['games'][0]['availability'],'checking')
        self.assertNotIn('signature',dock.snapshot['games'][0])
        self.assertFalse(dock.snapshot['session'].get('running'))

    def test_artwork_request_during_first_scan_never_reads_physical_fat(self):
        dock=Dock(self.data)
        with patch.object(dock,'root',return_value=None), \
             patch.object(dock,'games',
                          side_effect=AssertionError('No duplicate game scan')), \
             patch('dock.read_icon',
                   side_effect=AssertionError('No PSP content reads')):
            self.assertIsNone(dock.icon('a'*24))

    def test_new_device_still_requires_full_identity_verification(self):
        dock=Dock(self.data)
        self.assertFalse(dock.snapshot.get('can_launch_game',False))
        self.assertNotEqual(dock.snapshot['games'][0]['availability'],'ready')


if __name__=='__main__':
    unittest.main()
