"""Persistently skip a specific missing-games offer without USB copies."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock


class MissingGamesChoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.psp=self.root/'psp'
        (self.psp/'PSP/SAVEDATA').mkdir(parents=True)
        (self.psp/'ISO').mkdir()
        (self.psp/'ISO/WantToPlayLater.iso').write_bytes(b'fixture only')
        self.dock=Dock(self.root/'data')
        self.dock.settings({'psp_root':str(self.psp)})
        self.dock.state()

    def tearDown(self):
        self.tmp.cleanup()

    def test_dock_remembers_skip_across_restart_without_copying(self):
        self.assertEqual(self.dock.copy_policy(),'ask')
        with patch.object(self.dock,'transfer',
                          side_effect=AssertionError('No implicit transfer')):
            self.assertEqual(self.dock.choose_copy_policy('skip_missing'),'skip_missing')
        self.assertEqual(self.dock.copy_policy(),'skip_missing')
        self.assertFalse((Path(self.dock.config['library'])/
                          'ISO/WantToPlayLater.iso').exists())
        again=Dock(self.root/'data')
        again.settings({'psp_root':str(self.psp)})
        self.assertEqual(again.state()['copy_policy'],'skip_missing')

    def test_new_missing_title_requires_a_new_choice(self):
        self.dock.choose_copy_policy('skip_missing')
        (self.psp/'ISO/NewGame.iso').write_bytes(b'new fixture')
        self.dock.games()
        self.assertEqual(self.dock.copy_policy(),'ask')

    def test_game_became_ready_invalidates_old_missing_offer(self):
        self.dock.choose_copy_policy('skip_missing')
        game_id=self.dock.games()[0]['id']
        self.dock.transfer([game_id])
        self.assertEqual(self.dock.copy_policy(),'ask')
        self.assertTrue(self.dock.games()[0]['availability']=='ready')

    def test_wrong_choice_and_disconnected_mode_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'desconocida'):
            self.dock.choose_copy_policy('auto-copy')
        self.dock.settings({'psp_root':''})
        with patch('dock.windows_devices',return_value=[]):
            with self.assertRaisesRegex(ValueError,'pendientes'):
                self.dock.choose_copy_policy('skip_missing')

    def test_return_session_does_not_modify_preferences(self):
        self.dock.session['pending_return']=True
        with self.assertRaisesRegex(ValueError,'fase'):
            self.dock.choose_copy_policy('skip_missing')
        self.assertFalse((self.dock.data/'copy-decisions.json').exists())


if __name__=='__main__':
    unittest.main()
