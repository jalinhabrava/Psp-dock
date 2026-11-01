import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from dock import service_fingerprint
import start_dock


class ExistingDockWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=Path(self.tmp.name)/'live'
        self.data.mkdir()
        (self.data/'session.json').write_text('{"running":false}',encoding='utf-8')
        (self.data/'library').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_service(self, port=59336, *, fingerprint=None):
        record={'product':'psp-dock','port':port,'pid':9123,'demo':False,
                'ui_lifecycle':True,'ui_clients':1,'closing':False,
                'ui_connected_once':True,'native_handles':[444],
                'data_fingerprint':fingerprint or service_fingerprint(self.data)}
        (self.data/'server.json').write_text(json.dumps(record),encoding='utf-8')
        return record

    def response(self, data):
        stream=MagicMock()
        stream.__enter__.return_value=__import__('io').BytesIO(
            json.dumps(data).encode('utf-8'))
        return stream

    def test_valid_existing_server_reuses_same_data_without_double_start(self):
        expected=self.write_service()
        with patch('start_dock.urllib.request.urlopen',
                   return_value=self.response(expected)) as web, \
             patch.object(start_dock,'serve',
                          side_effect=AssertionError('A second live instance forbidden')), \
             patch.object(start_dock,'launch_xmb_window') as window:
            result=start_dock.launch(self.data)
        self.assertEqual(result,'already_running')
        web.assert_called_once()
        self.assertIn('/api/health',web.call_args.args[0])
        window.assert_called_once_with('http://127.0.0.1:59336',app_window=True,
                                       reuse_existing=True,include_handles={444})

    def test_spoofed_or_stale_service_cannot_be_reused(self):
        valid=self.write_service()
        bad={**valid,'data_fingerprint':'f'*64}
        self.assertIsNone(start_dock.existing_server(
            self.data,opener=lambda *a,**k:self.response(bad)))
        self.write_service(fingerprint='f'*64)
        with patch('start_dock.urllib.request.urlopen',
                   side_effect=AssertionError('Never check wrong folder')):
            self.assertIsNone(start_dock.existing_server(self.data))

    def test_invalid_ports_and_demo_are_not_reused(self):
        for port in (-1,1,65536,'59336',0):
            with self.subTest(port=port):
                self.write_service(port=port)
                self.assertIsNone(start_dock.existing_server(self.data))
        self.write_service()
        file=self.data/'server.json'
        record=json.loads(file.read_text())
        record['demo']=True
        file.write_text(json.dumps(record))
        self.assertIsNone(start_dock.existing_server(self.data))

    def test_missing_previous_server_starts_just_one(self):
        with patch.object(start_dock,'serve') as start, \
             patch.object(start_dock,'launch_xmb_window') as window:
            self.assertEqual(start_dock.launch(self.data),'started')
        start.assert_called_once()
        self.assertEqual(start.call_args.args[1:],(0,True,False,True))
        self.assertEqual(start.call_args.kwargs,{'ui_lifecycle':True})
        window.assert_not_called()

    def test_live_server_without_ui_opens_its_url_not_a_stale_title(self):
        record={**self.write_service(),'ui_clients':0,'native_handles':[]}
        with patch('start_dock.urllib.request.urlopen',
                   return_value=self.response(record)), \
             patch.object(start_dock,'serve') as start, \
             patch.object(start_dock,'launch_xmb_window') as window:
            self.assertEqual(start_dock.launch(self.data),'already_running')
        start.assert_not_called()
        window.assert_called_once_with('http://127.0.0.1:59336',app_window=True,
                                       reuse_existing=False,include_handles=set())

    def test_loading_first_window_does_not_spawn_a_second_browser(self):
        record={**self.write_service(),'ui_clients':0,'ui_connected_once':False,
                'native_handles':[]}
        with patch('start_dock.urllib.request.urlopen',return_value=self.response(record)), \
             patch.object(start_dock,'serve') as start, \
             patch.object(start_dock,'launch_xmb_window') as window:
            self.assertEqual(start_dock.launch(self.data),'starting')
        start.assert_not_called()
        window.assert_not_called()

    def test_connected_tab_cannot_authorize_reusing_orphan_title(self):
        record={**self.write_service(),'native_handles':[]}
        orphan={'handle':333,'title':'PSP Dock',
                'window_class':'Chrome_WidgetWin_1','visible':True}
        with patch('start_dock.urllib.request.urlopen',return_value=self.response(record)), \
             patch('dock.Path.is_file',return_value=True), \
             patch('dock.subprocess.Popen') as spawn, \
             patch('dock.threading.Thread') as thread, \
             patch('xmb_window.enumerate_xmb_windows',return_value=[orphan]), \
             patch('xmb_window.maximize_existing_xmb',return_value=True) as focus:
            self.assertEqual(start_dock.launch(self.data),'already_running')
        focus.assert_not_called()
        self.assertIn('--app=http://127.0.0.1:59336',spawn.call_args.args[0])
        self.assertEqual(thread.call_args.kwargs['kwargs']['exclude_handles'],{333})

    def test_blank_data_is_not_created_or_used(self):
        target=Path(self.tmp.name)/'new-empty-live'
        with patch.object(start_dock,'serve') as start:
            with self.assertRaisesRegex(ValueError,'sesión'):
                start_dock.launch(target)
        self.assertFalse(target.exists())
        start.assert_not_called()

    def test_closing_service_is_not_reused(self):
        record={**self.write_service(),'closing':True}
        self.assertIsNone(start_dock.existing_server(
            self.data,opener=lambda *a,**k:self.response(record)))

    def test_manual_first_install_can_create_default_data_when_no_live_exists(self):
        with patch.dict('os.environ',{'LOCALAPPDATA':self.tmp.name}), \
             patch.object(start_dock,'serve') as start:
            self.assertEqual(start_dock.launch(),'started')
        self.assertEqual(start.call_args.args[0].data,
                         (Path(self.tmp.name)/'PSP-Dock/live').resolve())


if __name__=='__main__':
    unittest.main()
