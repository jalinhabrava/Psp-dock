"""Reject cloned catalogue entries; keep links to real retro/PC ports only."""
import json
import tempfile
import unittest
from pathlib import Path

import dock


class OriginalPortDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='pspdock-original-'))
        self.addCleanup(__import__('shutil').rmtree, self.root)
        self.dock = dock.Dock(self.root / 'live')

    def test_manifest_excludes_imitation_catalogue_and_web_native_minigames(self):
        manifest = json.loads((dock.ROOT / 'web/ports.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['directory'], 'https://ported2browser.com/')
        self.assertEqual(manifest['version'], 1)
        self.assertEqual(len(manifest['games']), 5)
        self.assertEqual(len({g['id'] for g in manifest['games']}), 5)
        self.assertFalse({'hexgl', '2048'} & {g['id'] for g in manifest['games']})
        self.assertTrue(all(g['platform'] in ('PC', 'PS1', 'PS2') for g in manifest['games']))
        text = json.dumps(manifest)
        self.assertNotIn('ported2browser.sbs',text)
        self.assertNotIn('ported2browser.org',text)
        self.assertEqual(next(g['url'] for g in manifest['games']
                              if g['id']=='nfs-underground-2'),
                         'https://nfsu2.xyz/')
        self.assertEqual(next(g['url'] for g in manifest['games']
                              if g['id']=='wipeout'),
                         'https://phoboslab.org/wipegame/')
        self.assertFalse((dock.ROOT/'web/catalog-snapshot.json').exists())

    def test_iso_path_setting_is_metadata_only_and_never_updates_psp_session(self):
        folder=self.root/'owned images'
        folder.mkdir()
        image=folder/'Dragon Ball Z - Budokai Tenkaichi 3 (USA).iso'
        image.write_bytes(b'test-fixture')
        before_session=self.dock.session.copy()
        result=self.dock.set_web_iso_folder(str(folder))
        self.assertTrue(result['configured'])
        listing=self.dock.web_iso_library()
        self.assertEqual(listing['count'], 1)
        self.assertEqual(listing['entries'][0]['name'], image.name)
        self.assertNotIn('path',listing['entries'][0])
        matched=self.dock.web_iso_matches('budokai-tenkaichi-3')
        self.assertFalse(matched['matches']['confirmed'])
        self.assertEqual(matched['matches']['candidate_count'], 1)
        self.assertEqual(self.dock.session,before_session)
        self.dock.set_web_iso_folder('')
        self.assertFalse(self.dock.web_iso_library()['configured'])
        self.assertTrue(image.exists())


if __name__=='__main__':
    unittest.main()
