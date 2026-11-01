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

    def tearDown(self):
        self.tmp.cleanup()

    def write_service(self, port=59336, *, fingerprint=None):
        record={'product':'psp-dock','port':port,'pid':9123,'demo':False,
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
        window.assert_called_once_with('http://127.0.0.1:59336',app_window=True)

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
        window.assert_not_called()


if __name__=='__main__':
    unittest.main()
