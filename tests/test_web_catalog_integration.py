"""PSP Dock API models for huge web discovery and user-owned ISO metadata."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from dock import Dock
from psp_dock.media.web_port_catalog import CatalogError


class _FakeResponse:
    status = 200
    def __init__(self,data):
        self.data=data
    def read(self, count):
        return self.data[:count]
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False


class WebCatalogIntegration(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dock=Dock(Path(self.folder.name)/'live')

    def test_metadata_only_full_catalog_preserves_known_game(self):
        catalog=self.dock.web_catalog()
        self.assertGreaterEqual(catalog['catalog_total'],1400)
        self.assertEqual(len(catalog['games']),catalog['catalog_total'])
        self.assertEqual(len({g['id'] for g in catalog['games']}),
                         catalog['catalog_total'])
        nfs=next(g for g in catalog['games']
                 if g['id']=='nfs-underground-2')
        self.assertEqual(nfs['url'],'https://nfsu2.xyz/')
        directory=[g for g in catalog['games']
                   if g.get('launch_state')=='catalog_only']
        self.assertGreaterEqual(len(directory),1300)
        self.assertTrue(all('url' not in g for g in directory))

    def test_candidate_fetch_requires_known_id_and_source_page(self):
        for invalid in ('../settings.json','http://localhost','', 'a'*100):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.dock.resolve_web_catalog_port(invalid)
        sample='<html><a class="button" href="https://classic.minecraft.net/">Open original game</a></html>'
        with patch('urllib.request.build_opener') as browser:
            browser.return_value.open.return_value=_FakeResponse(sample.encode())
            result=self.dock.resolve_web_catalog_port('minecraft-classic')
        self.assertEqual(result['url'],'https://classic.minecraft.net/')
        self.assertEqual(result['launch_state'],'unverified')
        self.assertNotIn('file_path',result)
        self.assertEqual(self.dock.web_port_candidates['minecraft-classic'],result)
        with self.assertRaises(ValueError):
            self.dock.resolve_web_catalog_port('nfs-underground-2')

    def test_user_iso_folder_is_metadata_only_and_does_not_touch_saves(self):
        folder=Path(self.folder.name)/'owned-isos'
        folder.mkdir()
        (folder/'Dragon Ball Z - Budokai Tenkaichi 3 (USA).iso').write_bytes(b'synthetic')
        saved=self.dock.session.copy()
        result=self.dock.set_web_iso_folder(str(folder))
        self.assertTrue(result['configured'])
        listing=self.dock.web_iso_library()
        self.assertEqual(listing['count'],1)
        self.assertEqual(listing['entries'][0]['size'],9)
        self.assertEqual(listing['entries'][0]['extension'],'.iso')
        self.assertNotIn('path',listing['entries'][0],
                         'No private absolute image path may enter the HTTP response')
        matched=self.dock.web_iso_matches('budokai-tenkaichi-3')
        self.assertTrue(matched['configured'])
        self.assertFalse(matched['matches']['confirmed'])
        self.assertEqual(self.dock.session,saved)
        self.dock.set_web_iso_folder('')
        self.assertFalse(self.dock.web_iso_library()['configured'])
        self.assertTrue((folder/'Dragon Ball Z - Budokai Tenkaichi 3 (USA).iso').exists())


if __name__=='__main__':
    unittest.main()
