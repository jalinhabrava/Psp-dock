import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

from dock import Dock
from psp_dock.media.official_port_inventory import load_inventory
from psp_dock.media.preview_web_ports import build_preview_manifest

ROOT = Path(__file__).resolve().parents[1]


class PreviewManifestTests(unittest.TestCase):
    def setUp(self):
        self.inventory = load_inventory(ROOT)

    def test_shipped_inventory_keeps_states_and_source_identity(self):
        manifest = build_preview_manifest(self.inventory)
        self.assertEqual(len(manifest['games']), 330)
        self.assertEqual(sum(g['preview_allowed'] for g in manifest['games']), 146)
        self.assertTrue(all(g['category'] == 'port_candidate' for g in manifest['games'] if g['preview_allowed']))
        self.assertEqual(sum(g['category'] == 'port_candidate' and g['preview_allowed']
                             for g in manifest['games']), 146)
        for source, result in zip(self.inventory['games'], manifest['games']):
            for key in ('id', 'direct_url', 'category', 'verification_state', 'link_status',
                        'controls_declared', 'requires_original_files', 'requires_disc_image'):
                self.assertEqual(result[key], source[key])
            self.assertFalse(result['runtime_verified'])
            self.assertFalse(result['gamepad_tested'])
            if not result['preview_allowed']:
                self.assertIsNone(result['url'])
                self.assertTrue(result['preview_block_reason'])
        self.assertNotIn('preview_allowed', self.inventory['games'][0])

    def test_unsafe_or_ambiguous_destinations_are_retained_but_blocked(self):
        source = next(g for g in self.inventory['games'] if g['link_status'] == 'active' and g['category'] == 'port_candidate')
        for url in ('http://phoboslab.org/wipegame/', 'https://127.0.0.1/',
                    'https://localhost/', 'https://pc.local/', 'https://ported2browser.com/',
                    'https://user:password@phoboslab.org/', 'https://phoboslab.org:444/',
                    'https://phoboslab.org/?token=secret', 'https://phoboslab.org\\@localhost/'):
            with self.subTest(url=url):
                game = {**source, 'direct_url': url, 'direct_candidates': [url]}
                result = build_preview_manifest({**self.inventory, 'games': [game]})['games'][0]
                self.assertFalse(result['preview_allowed'])
                self.assertEqual(result['direct_url'], url)
        game = {**source, 'direct_candidates': [source['direct_url'], 'https://phoboslab.org/']}
        self.assertFalse(build_preview_manifest({**self.inventory, 'games': [game]})['games'][0]['preview_allowed'])

    def test_public_queries_and_fragments_are_preserved(self):
        source = next(g for g in self.inventory['games'] if g['link_status'] == 'active' and g['category'] == 'port_candidate')
        url = 'https://scummvm.kuendig.io/?game=demo#game=loom'
        game = {**source, 'direct_url': url, 'direct_candidates': [url]}
        self.assertEqual(build_preview_manifest({**self.inventory, 'games': [game]})['games'][0]['url'], url)

    def test_wrong_source_identity_and_duplicates_fail_closed(self):
        for change in ({'source': 'https://ported2browser.org/'},
                       {'games': [self.inventory['games'][0]] * 2},
                       {'games': [{**self.inventory['games'][0], 'detail_url': 'https://other.com/'}]}):
            with self.assertRaises(ValueError):
                build_preview_manifest({**self.inventory, **change})

    def test_files_and_pointer_requirements_are_not_certified_or_executed(self):
        manifest = build_preview_manifest(self.inventory)
        files = [g for g in manifest['games'] if g['requires_original_files'] and g['preview_allowed']]
        pointer = [g for g in manifest['games'] if g['verification_state'] == 'needs_pointer']
        self.assertTrue(files)
        self.assertTrue(pointer)
        self.assertTrue(all(not g['runtime_verified'] for g in files + pointer))
        self.assertTrue(all('file_path' not in g and 'auto_start' not in g for g in manifest['games']))

    def test_opt_in_requires_demo_before_creating_data(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / 'must-not-be-created'
            with self.assertRaises(ValueError):
                Dock(data, preview_web_ports=True)
            result = subprocess.run([sys.executable, str(ROOT / 'dock.py'), '--preview-web-ports',
                                     '--no-browser', '--data', str(data)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('requiere --demo', result.stderr)
            self.assertFalse(data.exists())


class PreviewHttpTests(unittest.TestCase):
    def test_default_off_and_explicit_demo_on_keep_curated_catalog_and_csp(self):
        snapshots = []
        for preview in (False, True):
            with self.subTest(preview=preview), tempfile.TemporaryDirectory() as temp:
                command = [sys.executable, str(ROOT / 'dock.py'), '--demo', '--no-browser',
                           '--port', '0', '--data', temp]
                if preview:
                    command.append('--preview-web-ports')
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    base = process.stdout.readline().strip().removeprefix('PSP Dock: ')
                    self.assertTrue(base.startswith('http://127.0.0.1:'))
                    def read(route):
                        with urllib.request.urlopen(base + route, timeout=10) as response:
                            return json.load(response)
                    health, state = read('/api/health'), read('/api/state')
                    self.assertEqual(health['preview_web_ports'], preview)
                    self.assertEqual(state['preview_web_ports'], preview)
                    snapshots.append(read('/api/web-ports'))
                    if preview:
                        manifest = read('/api/preview-web-ports')
                        self.assertEqual(len(manifest['games']), 330)
                        self.assertTrue(manifest['demo'])
                        self.assertTrue(manifest['preview_web_ports'])
                    else:
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            read('/api/preview-web-ports')
                        self.assertEqual(error.exception.code, 404)
                    with urllib.request.urlopen(base + '/', timeout=10) as response:
                        csp = response.headers['Content-Security-Policy']
                        self.assertIn("frame-ancestors 'none'", csp)
                        self.assertIn('frame-src https://phoboslab.org', csp)
                        self.assertNotIn('olivers.tools', csp)
                finally:
                    process.terminate()
                    process.wait(timeout=10)
                    process.stdout.close()
                    process.stderr.close()
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(next(g for g in snapshots[1]['games'] if g['id'] == 'nfs-underground-2')['url'],
                         'https://nfsu2.xyz/')


if __name__ == '__main__':
    unittest.main()
