import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from dock import make_demo
from tests.test_psp_artwork import PNG, write_fixture_iso

class HTTPContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        # A tiny but valid synthetic ISO for testing image extraction only;
        # this is a demo file, NEVER an executable game or a real Memory Stick.
        make_demo(cls.temp.name)
        cls.artwork_iso=(Path(cls.temp.name)/'sample-psp/ISO/Aurora Circuit.iso')
        write_fixture_iso(cls.artwork_iso)
        script=Path(__file__).resolve().parents[1]/'dock.py'
        cls.process=subprocess.Popen([sys.executable,str(script),'--demo','--no-browser','--port','0','--data',cls.temp.name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        cls.url=cls.process.stdout.readline().strip().removeprefix('PSP Dock: ')
        if not cls.url.startswith('http://127.0.0.1:'):
            raise RuntimeError('Server failed: '+cls.process.stderr.read())
        cls.token=json.load(urllib.request.urlopen(cls.url+'/api/token'))['token']
        deadline=time.monotonic()+20
        while json.load(urllib.request.urlopen(cls.url+'/api/state')).get('scanning'):
            if time.monotonic()>deadline:
                raise RuntimeError('Initial USB scan did not finish')
            time.sleep(.1)
    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.process.stdout.close()
        cls.process.stderr.close()
        cls.temp.cleanup()
    def request(self,path,body=None,token=True,origin=None):
        headers={}
        if body is not None:
            headers={'Content-Type':'application/json','Origin':origin or self.url}
            if token:
                headers['X-Dock-Token']=self.token
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=15) as res:
                return res.status,json.load(res)
        except urllib.error.HTTPError as e:
            return e.code,json.load(e)
    def test_state_labels_synthetic_and_transfers(self):
        status,state=self.request('/api/state')
        self.assertEqual(status,200)
        self.assertTrue(state['demo'])
        self.assertEqual(len(state['games']),3)
        missing=[g['id'] for g in state['games'] if g['availability']=='missing']
        self.assertEqual(self.request('/api/transfer',{'ids':missing})[0],200)
        _,state=self.request('/api/state')
        self.assertTrue(all(g['availability']=='ready' for g in state['games']))
    def test_post_without_token_rejected(self):
        self.assertEqual(self.request('/api/sync',{},token=False)[0],403)
        self.assertEqual(self.request('/api/ui-sound',{'sound':'cursor','at_ms':time.time()*1000},token=False)[0],403)

    def test_ui_sound_is_bounded_and_missing_original_is_silent(self):
        status, result = self.request('/api/ui-sound', {'sound':'cursor','at_ms':time.time()*1000})
        self.assertEqual(status, 200)
        self.assertFalse(result['played'])
        for sound in ('../snd_cursor', 'SystemAsterisk', 12):
            self.assertEqual(self.request('/api/ui-sound', {'sound':sound,'at_ms':time.time()*1000})[0],409)
        self.assertFalse(self.request('/api/ui-sound', {'sound':'cursor','at_ms':0})[1]['played'])

    def test_background_hd_does_not_read_a_game_or_start_a_usb_scan(self):
        before = self.artwork_iso.read_bytes()
        self.assertEqual(self.request('/api/media-hd/'+'0'*24)[0],202)
        self.assertEqual(self.request('/api/media-hd/../outside')[0],409)
        self.assertEqual(self.artwork_iso.read_bytes(),before)
    def test_foreign_origin_rejected(self):
        self.assertEqual(self.request('/api/sync',{},origin='https://example.com')[0],403)
    def test_demo_cannot_launch_synthetic_games(self):
        status,body=self.request('/api/launch',{'id':'anything'})
        self.assertEqual(status,409)
        self.assertIn('sintéticos',body['error'])

    def test_demo_cannot_request_fake_usb_return(self):
        status,body=self.request('/api/exit-dock',{})
        self.assertEqual(status,409)
        self.assertIn('sesión',body['error'])

    def test_demo_cannot_prewarm_real_ppsspp_or_grant_hid(self):
        status,body=self.request('/api/prepare-ppsspp',{})
        self.assertEqual(status,409)
        self.assertIn('demo',body['error'])
        state=self.request('/api/state')[1]
        self.assertFalse(state['runtime_prepared'])
        self.assertFalse(state['can_launch_game'])
        status,frame=self.request('/api/controller-input')
        self.assertEqual(status,200)
        self.assertFalse(frame['connected'])
        self.assertFalse(frame['accept'])

    def test_graphics_preview_does_not_touch_real_psp_or_modify_demo(self):
        state=self.request('/api/state')[1]
        ident=state['games'][0]['id']
        status,preview=self.request('/api/graphics/'+ident)
        self.assertEqual(status,200)
        self.assertEqual(preview['game_id'],ident)
        self.assertGreater(preview['plan']['internal_resolution'],0)
        self.assertFalse(preview['plan']['true_ultrawide_fov'])
        self.assertFalse(preview['plan']['gpu_ai_upscaling'])
        status,error=self.request('/api/graphics',
                                  {'id':ident,'quality':'ultra','aspect':'stretch'})
        self.assertEqual(status,409)
        self.assertIn('demo',error['error'])

    def test_progress_is_read_only_and_cancel_requires_live_job_and_token(self):
        status,body=self.request('/api/transfer-status')
        self.assertEqual(status,200)
        self.assertFalse(body['active'])
        self.assertIn(body['phase'],('idle','done','failed'))
        status,body=self.request('/api/cancel-transfer',{})
        self.assertEqual(status,409)
        self.assertIn('No hay',body['error'])
        self.assertEqual(self.request('/api/cancel-transfer',{},token=False)[0],403)
    def test_invalid_transfer_cannot_reuse_previous_success_status(self):
        status,body=self.request('/api/transfer',{'ids':['not-in-catalog']})
        self.assertEqual(status,409)
        self.assertIn('Selección',body['error'])
        status,progress=self.request('/api/transfer-status')
        self.assertEqual(status,200)
        self.assertFalse(progress['active'])
        self.assertEqual(progress['phase'],'failed')
        self.assertIn('Selección',progress['error'])
    def test_no_arbitrary_file_reads(self):
        self.assertEqual(self.request('/../dock.py')[0],404)

    def test_icon_endpoint_serves_verified_png_and_no_psp_writes(self):
        state=self.request('/api/state')[1]
        game=next(g for g in state['games'] if g['title']=='Aurora Circuit')
        before=self.artwork_iso.stat()
        original=self.artwork_iso.read_bytes()
        with urllib.request.urlopen(self.url+'/api/icon/'+game['id'],timeout=15) as response:
            self.assertEqual(response.status,200)
            self.assertEqual(response.headers['Content-Type'],'image/png')
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
            self.assertEqual(response.read(),PNG)
        self.assertEqual(self.artwork_iso.read_bytes(),original)
        self.assertEqual(self.artwork_iso.stat().st_mtime_ns,before.st_mtime_ns)
        self.assertTrue(list((Path(self.temp.name)/'artwork').glob('*.png')))

    def test_icon_rejects_invalid_ids_and_missing_images(self):
        state=self.request('/api/state')[1]
        missing=next(g for g in state['games'] if g['title']=='Lunar Expedition')
        for tail,expected in ((missing['id'],404),('bad-id',409),
                              ('%2e%2e%2fdock.py',409),('0'*24,404)):
            with self.subTest(tail=tail):
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(self.url+'/api/icon/'+tail,timeout=15)
                self.assertEqual(err.exception.code,expected)

if __name__=='__main__':
    unittest.main()
