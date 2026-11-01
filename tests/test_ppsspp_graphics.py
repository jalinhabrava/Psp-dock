import json
from pathlib import Path
import tempfile
import unittest

import ppsspp_graphics as g


class GraphicsTests(unittest.TestCase):
    def test_real_user_ultrawide_adaptive_preserves_original_ratio(self):
        screen=g.Monitor(3440,1440)
        auto=g.recommend(screen)
        self.assertTrue(auto['monitor']['ultrawide'])
        self.assertEqual(auto['internal_resolution'],5)
        self.assertEqual((auto['internal_width'],auto['internal_height']),(2400,1360))
        self.assertGreater(auto['side_border_px'],300)
        self.assertFalse(auto['true_ultrawide_fov'])
        self.assertIn('parche',auto['warning'])
        ultra=g.recommend(screen,quality='ultra')
        self.assertEqual(ultra['internal_resolution'],7)
        manual=g.recommend(screen,quality='manual',manual_scale=3)
        self.assertEqual(manual['internal_resolution'],3)
        stretch=g.recommend(screen,aspect='stretch')
        self.assertEqual(stretch['frame_width'],3440)
        self.assertEqual(stretch['side_border_px'],0)
        self.assertIn('deformarse',stretch['warning'])

    def test_validates_screens_and_choices(self):
        for width,height in ((0,1440),(3440,0),(22000,500),(True,1080)):
            with self.subTest(size=(width,height)),self.assertRaises(g.GraphicsError):
                g.Monitor(width,height)
        for q,a in (('dlss','fit'),('auto','ultrawide_magic'),('ultra',None)):
            with self.assertRaises(g.GraphicsError):
                g.recommend(g.Monitor(1920,1080),quality=q,aspect=a)

    def test_low_end_and_4k_scales(self):
        self.assertEqual(g.recommend(g.Monitor(1920,1080))['internal_resolution'],4)
        self.assertEqual(g.recommend(g.Monitor(3840,2160))['internal_resolution'],8)
        self.assertFalse(g.recommend(g.Monitor(3840,2160))['gpu_ai_upscaling'])

    def test_persistent_per_game_profiles_keep_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'profiles.json'
            game='0'*24
            self.assertEqual(g.per_game_choice(g.preferences(path),game),
                             {'quality':'auto','aspect':'fit','texture':'off'})
            obj=g.set_preference(path,game_id=game,quality='ultra',aspect='fit')
            self.assertEqual(obj['games'][game]['quality'],'ultra')
            g.set_preference(path,game_id=None,quality='balanced',aspect='stretch')
            self.assertEqual(g.per_game_choice(g.preferences(path),game)['quality'],'ultra')
            self.assertEqual(g.per_game_choice(g.preferences(path),'1'*24)['quality'],'balanced')
            with self.assertRaises(g.GraphicsError):
                g.set_preference(path,game_id='../outside',quality='auto',aspect='fit')
            self.assertFalse((Path(temp)/'outside').exists())

    def test_clearing_a_game_profile_returns_it_to_the_default(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'profiles.json'
            game='0'*24
            g.set_preference(path,game_id=game,quality='ultra',aspect='fit')
            g.set_preference(path,game_id=None,quality='balanced',aspect='fit')
            obj=g.clear_preference(path,game_id=game)
            self.assertNotIn(game,obj['games'])
            self.assertEqual(g.per_game_choice(g.preferences(path),game)['quality'],'balanced')
            g.clear_preference(path,game_id=game)  # idempotent
            with self.assertRaises(g.GraphicsError):
                g.clear_preference(path,game_id='../outside')

    def test_texture_processing_modes_are_explicit_and_never_call_dlss(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'profiles.json'
            game='a'*24
            db=g.set_preference(path,game_id=game,quality='quality',
                                aspect='fit',texture='xbrz3')
            choice=g.per_game_choice(g.preferences(path),game)
            self.assertEqual(choice['texture'],'xbrz3')
            plan=g.recommend(g.Monitor(3440,1440),**choice)
            self.assertTrue(plan['texture_upscale_on_cpu'])
            self.assertEqual(plan['texture_upscale_algorithm'],'xBRZ')
            self.assertFalse(plan['gpu_ai_upscaling'])
            self.assertFalse(plan['true_ultrawide_fov'])
            self.assertEqual(g.recommend(g.Monitor(3440,1440),
                                         texture='bicubic2')['texture_upscale_algorithm'],'Bicubic')
            for invalid in ('dlss','nvidia-vsr','upscale_128'):
                with self.assertRaises(g.GraphicsError):
                    g.set_preference(path,game_id=game,quality='auto',
                                     aspect='fit',texture=invalid)

    def test_built_in_ppsspp_tex_scaling_is_written_to_private_profile_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            ini=root/'private/PSP/SYSTEM/ppsspp.ini'
            ini.parent.mkdir(parents=True)
            ini.write_text('[Graphics]\nInternalResolution = 3\nTexScalingLevel = 1\n'
                           'TexScalingType = 0\n[Controls]\nSquare = 10-190\n')
            before=ini.read_bytes()
            pack=g.recommend(g.Monitor(3440,1440),texture='xbrz2')
            report=g.apply_private_ppsspp_ini(ini,plan=pack,
                                              backup_dir=root/'backups')
            self.assertTrue(report['changed'])
            self.assertIn('TexScalingLevel = 2',ini.read_text())
            self.assertIn('TexScalingType = 0',ini.read_text())
            self.assertIn('Square = 10-190',ini.read_text())
            self.assertEqual(Path(report['backup']).read_bytes(),before)
            plan=g.recommend(g.Monitor(3440,1440),texture='bicubic2')
            g.apply_private_ppsspp_ini(ini,plan=plan,backup_dir=root/'backups')
            self.assertIn('TexScalingType = 2',ini.read_text())
            plan['replace_textures']=True
            g.apply_private_ppsspp_ini(ini,plan=plan,backup_dir=root/'backups')
            self.assertIn('ReplaceTextures = True',ini.read_text())
            self.assertNotIn('SaveNewTextures = True',ini.read_text())

    def test_private_ini_has_verified_backup_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            ini=root/'private/PSP/SYSTEM/ppsspp.ini'
            ini.parent.mkdir(parents=True)
            original=b'[Graphics]\nInternalResolution = 3\nFrameSkip = 0\nExtra = KEEP\n\n[DisplayLayout.Landscape]\nDisplayStretch = False\n'
            ini.write_bytes(original)
            plan=g.recommend(g.Monitor(3440,1440),quality='quality')
            backups=root/'backups'
            result=g.apply_private_ppsspp_ini(ini,plan=plan,backup_dir=backups)
            self.assertTrue(result['changed'])
            self.assertEqual(Path(result['backup']).read_bytes(),original)
            self.assertIn('InternalResolution = 6',ini.read_text())
            self.assertIn('Extra = KEEP',ini.read_text())
            self.assertFalse(g.apply_private_ppsspp_ini(
                ini,plan=plan,backup_dir=backups)['changed'])
            self.assertEqual(len(list(backups.glob('*.ini'))),1)

if __name__=="__main__":
    unittest.main()
