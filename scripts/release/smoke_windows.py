"""Exercise the actual release executable without touching a connected PSP."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


def main(exe: Path) -> None:
    with tempfile.TemporaryDirectory(prefix='psp-dock-release-') as profile:
        env = {**os.environ, 'LOCALAPPDATA': profile}
        proc = subprocess.Popen([str(exe), '--demo', '--no-browser'], env=env,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        record = Path(profile) / 'PSP-Dock/demo/server.json'
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not record.exists():
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
            if not record.exists():
                log = Path(profile) / 'PSP-Dock/launcher.log'
                raise RuntimeError('Server did not start: ' +
                                   (log.read_text(encoding='utf-8') if log.exists() else 'no log'))
            port = json.loads(record.read_text(encoding='utf-8'))['port']
            root = f'http://127.0.0.1:{port}'
            def fetch(route: str):
                with urllib.request.urlopen(root + route, timeout=10) as response:
                    return response.status, response.read()
            status, health = fetch('/api/health')
            parsed = json.loads(health)
            assert status == 200 and parsed['product'] == 'psp-dock' and parsed['demo']
            status, state = fetch('/api/state')
            assert status == 200 and isinstance(json.loads(state), dict)
            status, raw_catalog = fetch('/api/web-ports')
            catalog = json.loads(raw_catalog)
            assert status == 200 and catalog['directory'] == 'https://ported2browser.com/'
            assert len(catalog['games']) == 5
            assert 'ported2browser.sbs' not in raw_catalog.decode('utf-8')
            assert 'ported2browser.org' not in raw_catalog.decode('utf-8')
            assert not any(g['id'] in ('2048','hexgl') for g in catalog['games'])
            assert next(g['url'] for g in catalog['games']
                        if g['id'] == 'nfs-underground-2') == 'https://nfsu2.xyz/'
            status, raw_isos = fetch('/api/web-iso-library')
            isos = json.loads(raw_isos)
            assert status == 200 and isos['configured'] is False
            assert isos['count'] == 0
            for path, marker in [('/', b'<html'), ('/style.css', b'{'),
                                 ('/i18n.js', b'DockI18n'), ('/app.js', b'function')]:
                status, body = fetch(path)
                assert status == 200 and marker in body, (path, status, len(body))
            assert (Path(profile) / 'PSP-Dock/demo/sample-psp').exists()
            print(json.dumps({'result': 'PASS', 'port': port,
                              'exe_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
                              'curated_ports': len(catalog['games']),
                              'iso_library_configured': isos['configured'],
                              'api': ['/api/health', '/api/state',
                                      '/api/web-ports', '/api/web-iso-library'],
                              'static': ['/', '/style.css', '/i18n.js', '/app.js']}, indent=2))
        finally:
            if proc.poll() is None:
                # --no-browser intentionally serves until stopped; end both
                # PyInstaller's parent bootloader and its child on Windows.
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, check=False)
                proc.wait(timeout=10)
            # An interrupted demo may leave server.json (the next launch
            # validates it against the actual running process).


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
