"""Texture-pack matching uses verified local disc IDs, never ROM filenames."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock
from tests.test_psp_artwork import write_fixture_iso
from tests.test_psp_game_metadata import fixture_sfo
from psp_artwork import SECTOR
from tests.test_psp_artwork import rec


class VerifiedGameTextureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        self.psp=self.home/'synthetic-psp'
        (self.psp/'PSP/SAVEDATA').mkdir(parents=True)
        self.game=self.psp/'ISO/Example.iso'
        write_fixture_iso(self.game)
        content=bytearray(self.game.read_bytes())
        sfo=fixture_sfo()
        entry=rec(b'PARAM.SFO;1',23,len(sfo),False)
        offset=21*SECTOR
        old=rec(b'ICON0.PNG;1',22,0,False)
        content[offset+len(old):offset+len(old)+len(entry)]=entry
        content[23*SECTOR:23*SECTOR+len(sfo)]=sfo
        self.game.write_bytes(content)
        self.dock=Dock(self.home/'data')
        self.dock.settings({'psp_root':str(self.psp)})
        self.id=self.dock.games()[0]['id']
        self.dock.transfer([self.id])

    def tearDown(self):
        self.temp.cleanup()

    def test_view_reports_own_profile_and_reset_follows_default(self):
        with patch('ppsspp_graphics.primary_monitor',
                   return_value=__import__('ppsspp_graphics').Monitor(1920,1080)):
            first=self.dock.graphics_view(self.id)
            self.assertFalse(first['custom'])
            self.assertEqual(first['default_choice']['quality'],'auto')
            own=self.dock.set_graphics_view({'id':self.id,'quality':'ultra','aspect':'fit','texture':'off'})
            self.assertTrue(own['custom'])
            self.assertEqual(own['choice']['quality'],'ultra')
            back=self.dock.set_graphics_view({'id':self.id,'reset':True})
            self.assertFalse(back['custom'])
            self.assertEqual(back['choice']['quality'],'auto')
            with self.assertRaises(ValueError):
                self.dock.set_graphics_view({'id':None,'reset':True})

    def test_verified_pc_game_disc_id_used_for_texture_pack(self):
        with patch('psp_game_metadata.read_metadata',
                   wraps=__import__('psp_game_metadata').read_metadata) as loader:
            overview=self.dock.graphics_view(self.id)
        self.assertEqual(overview['game_metadata']['disc_id'],'NPJH50053')
        self.assertFalse(overview['hd_texture_pack_installed'])
        self.assertEqual(loader.call_count,1)
        self.assertTrue(str(loader.call_args.args[0]).startswith(
            str(Path(self.dock.config['library']))))
        pack=Path(self.dock.config['memstick'])/'PSP/TEXTURES/NPJH50053'
        pack.mkdir(parents=True)
        (pack/'textures.ini').write_text('[options]\nversion = 1\n')
        self.assertTrue(self.dock.graphics_view(self.id)['hd_texture_pack_installed'])

    def test_metadata_preview_never_reads_nonlocal_missing_rom(self):
        # Another game exists only on PSP storage: viewing its graphics
        # choices may not read its PARAM.SFO over the physical USB bus.
        another=self.psp/'ISO/Large.iso'
        write_fixture_iso(another)
        missed=next(g for g in self.dock.games() if g['title']=='Large')
        with patch('psp_game_metadata.read_metadata',
                   side_effect=AssertionError('USB metadata read forbidden')):
            overview=self.dock.graphics_view(missed['id'])
        self.assertNotIn('game_metadata',overview)

    def test_graphics_preview_does_not_start_full_psp_inventory_during_attach(self):
        self.dock.cached_game_results=None
        with patch.object(self.dock,'games',
                          side_effect=AssertionError('No duplicate FAT inventory')):
            overview=self.dock.graphics_view(self.id)
        self.assertEqual(overview['plan']['monitor']['width']>0,True)
        self.assertNotIn('game_metadata',overview)


if __name__=='__main__':
    unittest.main()
