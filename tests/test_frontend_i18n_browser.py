"""Chromium smoke test for the real web app, without connecting PSP hardware.

Run: python -m unittest tests/test_frontend_i18n_browser.py
Skips if neither Chrome nor Edge is installed. Only uses the Python stdlib.
"""
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
CHROME_PATHS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]


def find_browser():
    return next((str(p) for p in CHROME_PATHS if Path(p).exists()), None) or (
        shutil.which('chromium') or shutil.which('google-chrome') or shutil.which('microsoft-edge')
    )


FAKE_STATE = {
    'demo': True, 'selected_device': None, 'devices': [],
    'controller_status': {'connected': False, 'mode': 'none'},
    'games': [{'id': 'USER_GAME_1', 'title': 'Aurora Circuit', 'format': 'ISO',
               'availability': 'ready', 'size': 1024},
              {'id': 'USER_GAME_2', 'title': 'Juegos', 'format': 'ISO',
               'availability': 'ready', 'size': 1024}],
    'saves': {'actions': [], 'conflicts': [], 'unchanged': 0},
    'session': {'running': False}, 'retro': {'roms': [], 'emulators': []},
    'retro_saves': {'rows': [], 'pending': 0}, 'product_flow': {'enabled': False},
}


class FakeDockHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == '/api/state':
            payload = json.dumps(FAKE_STATE).encode('utf-8')
            return self._answer(payload, 'application/json')
        if path.path == '/api/settings':
            return self._answer(b'{}', 'application/json')
        if path.path == '/':
            html = (WEB / 'index.html').read_text(encoding='utf-8')
            action = parse_qs(path.query).get('action', [''])[0]
            if action == 'change-language':
                # Exercise the actual XMB Settings item and its value picker.
                injected = """<script>window.addEventListener('load', () => {
                  setTimeout(() => {
                    xmbGoto('ajustes');
                    xmb.nav.idx.ajustes = 0;
                    xmbRender();
                    xmbPress('accept');
                    if (xmb.nav.side) {
                      xmb.nav.side.i = 1;
                      xmbPress('accept');
                    }
                    document.body.dataset.languageTest = DockI18n.locale();
                  }, 600);
                });</script>"""
                html = html.replace('</body>', injected + '</body>')
            return self._answer(html.encode('utf-8'), 'text/html; charset=utf-8')
        return super().do_GET()

    def _answer(self, payload, mime):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@unittest.skipUnless(find_browser(), 'Chrome/Edge/Chromium unavailable')
class BrowserLocaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeDockHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def browser_dom(self, profile, locale='en-US', action=''):
        url = self.base + ('?action=' + action if action else '')
        command = [find_browser(), '--headless=new', '--disable-gpu', '--no-first-run',
                   '--no-default-browser-check', '--disable-extensions',
                   '--user-data-dir=' + str(profile), '--lang=' + locale,
                   '--virtual-time-budget=2600', '--dump-dom', url]
        result = subprocess.run(command, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=35, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-600:])
        self.assertIn('id="dock-connect-intro"', result.stdout)
        return result.stdout

    def test_default_languages_and_game_title(self):
        with tempfile.TemporaryDirectory() as directory:
            english = self.browser_dom(Path(directory) / 'en', 'en-US')
            self.assertRegex(english, r'<html lang="en"')
            self.assertIn('>Settings</span>', english)
            self.assertIn('Aurora Circuit', english)
            self.assertIn('data-i18n-skip="true">Juegos</span>', english,
                          'A game named Juegos must not become Games')
            self.assertIn('>Game', english)
            spanish = self.browser_dom(Path(directory) / 'es', 'es-ES')
            self.assertRegex(spanish, r'<html lang="es"')
            self.assertIn('>Ajustes</span>', spanish)
            self.assertIn('Aurora Circuit', spanish)

    def test_xmb_language_override_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile'
            switched = self.browser_dom(profile, 'en-US', 'change-language')
            self.assertIn('data-language-test="es"', switched)
            self.assertIn('<html lang="es"', switched)
            again = self.browser_dom(profile, 'en-US')
            self.assertIn('<html lang="es"', again)
            self.assertIn('>Ajustes</span>', again)


if __name__ == '__main__':
    unittest.main()
