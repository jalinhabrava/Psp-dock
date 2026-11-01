"""No-click preflight is allowed ONLY when zero save copies are necessary."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock


class AutoCleanPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.psp=self.home/'psp-fixture'
        self.game=self.psp/'ISO/Example.iso'
        self.game.parent.mkdir(parents=True)
        self.game.write_bytes(b'fake ISO for metadata tests')
        self.slot=self.psp/'PSP/SAVEDATA/GAME01/DATA.BIN'
        self.slot.parent.mkdir(parents=True)
        self.slot.write_bytes(b'unmodified save data')
        self.dock=Dock(self.home/'data')
        self.dock.settings({'psp_root':str(self.psp)})
        self.dock.transfer([self.dock.games()[0]['id']])

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_identical_saves_prepare_pc_metadata_with_no_copy(self):
        local=Path(self.dock.config['memstick'])/'PSP/SAVEDATA/GAME01/DATA.BIN'
        local.parent.mkdir(parents=True)
        local.write_bytes(self.slot.read_bytes())
        original=self.slot.read_bytes()
        with patch.object(self.dock, 'sync',side_effect=AssertionError('copy forbidden')), \
             patch('dock.copy_slot',side_effect=AssertionError('PSP write forbidden')):
            self.assertTrue(self.dock.prepare_identical_saves_without_copy())
            self.assertFalse(self.dock.prepare_identical_saves_without_copy())
        self.assertEqual(self.slot.read_bytes(),original)
        self.assertEqual(local.read_bytes(),original)
        self.assertTrue(self.dock.session.get('prepared_volume'))
        text=(self.dock.data/'events.jsonl').read_text(encoding='utf-8')
        self.assertIn('saves_identical_auto_prepared',text)

    def test_missing_pc_save_requires_explicit_user_consent(self):
        self.assertFalse(self.dock.prepare_identical_saves_without_copy())
        self.assertNotIn('prepared_volume',self.dock.session)
        self.assertEqual(self.dock.save_plan()['actions'][0]['direction'],'to_pc')

    def test_diverged_save_cannot_auto_prepare(self):
        local=Path(self.dock.config['memstick'])/'PSP/SAVEDATA/GAME01/DATA.BIN'
        local.parent.mkdir(parents=True)
        local.write_bytes(b'CHANGED PC')
        self.assertFalse(self.dock.prepare_identical_saves_without_copy())
        self.assertEqual(self.slot.read_bytes(),b'unmodified save data')

    def test_watch_uses_no_copy_preparation_then_mock_prewarm(self):
        local=Path(self.dock.config['memstick'])/'PSP/SAVEDATA/GAME01/DATA.BIN'
        local.parent.mkdir(parents=True)
        local.write_bytes(self.slot.read_bytes())
        def finish(_delay):
            self.dock.stop.set()
        with patch.object(self.dock.stop,'wait',side_effect=finish), \
             patch.object(self.dock,'sync',side_effect=AssertionError('No sync')), \
             patch.object(self.dock,'prepare_runtime',return_value={'ready':True}) as warm:
            self.dock.watch()
        self.assertTrue(self.dock.snapshot['preflight']['saves_prepared'])
        warm.assert_called_once()
        self.assertFalse(self.dock.snapshot['session']['pending_return'])

    def test_return_session_must_never_auto_prepare_baseline(self):
        local=Path(self.dock.config['memstick'])/'PSP/SAVEDATA/GAME01/DATA.BIN'
        local.parent.mkdir(parents=True)
        local.write_bytes(self.slot.read_bytes())
        self.dock.session['pending_return']=True
        self.assertFalse(self.dock.prepare_identical_saves_without_copy())
        self.assertNotIn('prepared_volume',self.dock.session)

if __name__=='__main__':
    unittest.main()
