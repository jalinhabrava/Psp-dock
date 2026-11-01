from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile

import dock


class AppWindowTests(unittest.TestCase):
    def test_invalid_or_external_urls_do_not_open_browsers(self):
        for url in ('https://evil.invalid','http://localhost:1',
                    'http://127.0.0.1:3333/a/../b','file:///C:/'):
            with self.subTest(url=url), \
                 patch('dock.webbrowser.open') as browse:
                with self.assertRaises(ValueError):
                    dock.launch_xmb_window(url,app_window=True)
                browse.assert_not_called()

    def test_chrome_app_mode_fullscreen_without_extra_user_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            exe=root/'Google/Chrome/Application/chrome.exe'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'SYNTHETIC fixture never executed')
            with patch.dict('dock.os.environ',{'PROGRAMFILES':str(root),
                                               'PROGRAMFILES(X86)':str(root)}), \
             patch('dock.subprocess.Popen') as popen, \
             patch('xmb_window.maximize_existing_xmb',return_value=False), \
             patch('dock.threading.Thread') as thread:
                dock.launch_xmb_window('http://127.0.0.1:59336',
                                       app_window=True)
            popen.assert_called_once()
            args=popen.call_args.args[0]
            self.assertEqual(args[0],str(exe))
            self.assertIn('--app=http://127.0.0.1:59336',args)
            self.assertIn('--start-maximized',args)
            self.assertFalse(any(arg.startswith('--user-data-dir') for arg in args))
            thread.return_value.start.assert_called_once()

    def test_open_same_xmb_reuses_window_instead_of_starting_chrome(self):
        with patch('xmb_window.maximize_existing_xmb',return_value=True), \
             patch('dock.subprocess.Popen') as popen:
            self.assertTrue(dock.launch_xmb_window('http://127.0.0.1:59336',
                                                   app_window=True))
        popen.assert_not_called()

    def test_normal_browser_mode_still_available(self):
        with patch('dock.webbrowser.open',return_value=True) as opened:
            self.assertTrue(dock.launch_xmb_window('http://127.0.0.1:59336'))
            opened.assert_called_once_with('http://127.0.0.1:59336')


if __name__=='__main__':
    unittest.main()
