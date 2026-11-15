"""Evidence levels, source identity and metadata-only ISO matching."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dock
from psp_dock.media.official_port_inventory import build_inventory, category_for

ID = '01M6FJS3FP9ER9BWP5FTP5XP7H'
URL = 'https://socomlegacy.com/'


def fixture():
    return {'source': 'https://ported2browser.com/',
            'kind': 'existing_chrome_public_dom', 'reported_total': 1,
            'listing_complete': True, 'games': [{
                'id': ID, 'title': 'SOCOM II',
                'detail_url': 'https://ported2browser.com/ports/' + ID,
                'direct_candidates': [URL], 'original_platforms': ['PlayStation 2'],
                'controls': {'gamepad': True, 'keyboard': False},
                'requires_disc_image': True, 'observed_at': '2026-11-15T11:00:00Z',
                'description': 'Requires your own disc image.'}]}


class OfficialInventoryTests(unittest.TestCase):
    def test_http_and_directory_badge_do_not_prove_gameplay(self):
        inventory = build_inventory(fixture(), {URL: {'link_status': 'active',
                                                     'http_status': 200}})
        game = inventory['games'][0]
        self.assertEqual(game['link_status'], 'active')
        self.assertEqual(game['verification_state'], 'requires_original_files')
        self.assertFalse(game['runtime_verified'])
        self.assertFalse(game['gamepad_tested'])
        self.assertTrue(game['controls_declared']['gamepad'])
        self.assertIsNone(game['additional_controls']['second_stick'])

    def test_broken_badge_not_overruled_by_parked_domain_200(self):
        capture = fixture()
        capture['games'][0]['broken'] = True
        game = build_inventory(capture, {URL: {'link_status': 'active'}})['games'][0]
        self.assertEqual(game['verification_state'], 'unavailable')
        self.assertEqual(game['link_status'], 'broken')

    def test_multiple_links_remain_unambiguous_and_403_pending(self):
        capture = fixture()
        capture['games'][0].update(requires_disc_image=False, description='',
                                   direct_candidates=[URL, 'https://example.com/game'])
        game = build_inventory(capture, {URL: {'link_status': 'pending',
                                               'http_status': 403}})['games'][0]
        self.assertIsNone(game['direct_url'])
        self.assertEqual(len(game['direct_candidates']), 2)
        self.assertEqual(game['link_status'], 'pending')
        self.assertEqual(game['verification_state'], 'direct_unverified')

    def test_categories_do_not_inflate_ports(self):
        self.assertEqual(category_for({'original_platforms': ['Web browser']}), 'web_native')
        self.assertEqual(category_for({'original_platforms': ['Windows'],
                                       'flags': ['emulator-tag']}), 'emulator')
        self.assertEqual(category_for({'original_platforms': ['PlayStation 2'],
                                       'flags': ['wip-tag']}), 'wip')
        self.assertEqual(category_for({}), 'unclassified')

    def test_duplicate_wrong_source_and_private_links(self):
        capture = fixture()
        capture['source'] = 'https://ported2browser.org/'
        with self.assertRaises(ValueError):
            build_inventory(capture)
        capture = fixture()
        capture['games'].append(copy.deepcopy(capture['games'][0]))
        with self.assertRaises(ValueError):
            build_inventory(capture)
        capture = fixture()
        capture['games'][0]['direct_candidates'] = ['https://127.0.0.1/',
                                                    'https://ported2browser.com/']
        self.assertEqual(build_inventory(capture)['summary']['direct_url'], 0)

    def test_complete_requires_actual_count(self):
        capture = fixture()
        capture['reported_total'] = 330
        self.assertFalse(build_inventory(capture)['summary']['listing_complete'])

    def test_reviewed_files_override_missing_directory_disc_badge(self):
        capture = fixture()
        capture['games'][0].update(requires_disc_image=False,
                                  description='A PSP recompilation. Need your own disc dump.')
        review = {'detail_url': capture['games'][0]['detail_url'], 'direct_url': URL,
                  'required': True, 'disc_image': True, 'optional': False}
        result = build_inventory(capture, file_requirements={ID: review})['games'][0]
        self.assertTrue(result['requires_disc_image'])
        self.assertTrue(result['requires_original_files'])
        review['direct_url'] = 'https://example.com/wrong'
        with self.assertRaises(ValueError):
            build_inventory(capture, file_requirements={ID: review})

    def test_unrelated_reddit_headlines_cannot_create_requirements(self):
        capture = fixture()
        capture['games'][0].update(requires_disc_image=False,
            description='Latest on Reddit: Requires your own ROM. About this portHosted demo.')
        result = build_inventory(capture)['games'][0]
        self.assertFalse(result['requires_original_files'])
        self.assertEqual(result['description'], 'Hosted demo.')

    def test_rendered_scene_and_requirement_conflict_do_not_prove_gamepad(self):
        observation = {'url': URL, 'rendered_game_scene': True,
                       'requirements_conflict': True}
        result = build_inventory(fixture(), runtime_observations={ID: observation})
        game = result['games'][0]
        self.assertTrue(game['runtime_scene_observed'])
        self.assertTrue(game['requirements_conflict'])
        self.assertFalse(game['runtime_verified'])
        self.assertFalse(game['gamepad_tested'])
        self.assertEqual(result['summary']['runtime_scenes_observed'], 1)
        observation['url'] = 'https://example.com/wrong'
        with self.assertRaises(ValueError):
            build_inventory(fixture(), runtime_observations={ID: observation})

    def test_full_snapshot_keeps_all_evidence_and_reviewed_file_scopes(self):
        root = Path(__file__).resolve().parents[1]
        capture = json.loads((root/'docs/catalog/ported2browser-chrome-20261115.json').read_text(encoding='utf-8'))
        reviews = json.loads((root/'docs/catalog/ported2browser-file-requirements-20261115.json').read_text(encoding='utf-8'))
        result = build_inventory(capture, file_requirements=reviews)
        self.assertTrue(result['summary']['listing_complete'])
        self.assertEqual(result['summary']['listed'], 330)
        self.assertEqual(result['summary']['single_direct_url'], 330)
        self.assertEqual(result['summary']['requires_disc_image'], 3)
        self.assertEqual(result['summary']['requires_original_files'], 12)
        self.assertEqual(result['summary']['optional_original_files'], 7)

    def test_http_play_anchor_is_metadata_not_launch_authority(self):
        capture = fixture()
        game = capture['games'][0]
        game['direct_candidates'] = []
        game['raw_play_links'] = [{'href': 'http://xproger.info/projects/OpenLara/',
                                   'id': ID, 'page': '/ports/' + ID}]
        result = build_inventory(capture)['games'][0]
        self.assertEqual(result['direct_url'], 'http://xproger.info/projects/OpenLara/')
        self.assertFalse(result['runtime_verified'])

    def test_official_iso_match_reads_metadata_and_preserves_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / 'SOCOM II.iso'
            image.write_bytes(b'owned-test-image')
            app = dock.Dock(root / 'live')
            app.set_web_iso_folder(str(root))
            inventory = build_inventory(fixture())
            with patch('psp_dock.media.official_port_inventory.load_inventory',
                       return_value=inventory):
                result = app.web_iso_matches(ID)
            self.assertEqual(result['title'], 'SOCOM II')
            self.assertGreaterEqual(result['matches']['candidate_count'], 1)
            self.assertFalse(result['matches']['confirmed'])
            self.assertEqual(image.read_bytes(), b'owned-test-image')


if __name__ == '__main__':
    unittest.main()
