"""Launch or reconnect to the *same* PSP Dock LIVE instance on Windows.

Only one server can hold the LIVE folder's existing OS lock. The launcher
reads a host-local server record, verifies it with an HTTP health check,
and opens the XMB in an app-style window. Never launches a game or
changes USB modes simply because PSP has connected.
"""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import urllib.error
import urllib.request

from dock import (
    Dock, default_data_path, launch_xmb_window,
    service_fingerprint, serve, read_json,
)
from setup_startup import choose_live_dir


def _checked_server(data: Path, *, opener=None) -> dict | None:
    root=Path(data).resolve()
    record=read_json(root/'server.json',{})
    if not isinstance(record,dict) or record.get('product')!='psp-dock':
        return None
    port=record.get('port')
    if type(port) is not int or not 1024 <= port <= 65535:
        return None
    fingerprint=service_fingerprint(root)
    if record.get('data_fingerprint')!=fingerprint or record.get('demo'):
        return None
    url='http://127.0.0.1:'+str(port)
    try:
        provider=opener or urllib.request.urlopen
        with provider(url+'/api/health',timeout=3) as reply:
            result=json.load(reply)
        if (result.get('product')=='psp-dock' and
            result.get('demo') is False and
            result.get('port')==port and
            result.get('data_fingerprint')==fingerprint and
            result.get('closing') is not True):
            return {'url':url, 'health':result}
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        pass
    return None


def existing_server(data: Path, *, opener=None) -> str | None:
    current = _checked_server(data, opener=opener)
    return current['url'] if current else None


def launch(data: Path | None = None) -> str:
    local=Path(os.environ.get('LOCALAPPDATA', str(Path(__file__).parent)))
    data=(choose_live_dir(local, explicit=data) if data is not None
          else default_data_path(local))
    current=_checked_server(data)
    if current:
        health=current['health']
        if (health.get('ui_lifecycle') is True and
                health.get('ui_connected_once') is False and health.get('ui_clients') == 0):
            # The first browser is still loading. A second manual click
            # must not create another window before its first heartbeat.
            return 'starting'
        # A healthy ordinary tab does not identify a native app window.
        # Only HWNDs observed by this same service can be reused.
        observed=health.get('native_handles')
        handles=({value for value in observed if type(value) is int and value > 0}
                 if isinstance(observed,list) and len(observed) <= 8 else set())
        reuse=(health.get('ui_lifecycle') is True and bool(handles))
        launch_xmb_window(current['url'],app_window=True,reuse_existing=reuse,
                          include_handles=handles if reuse else set())
        return 'already_running'
    # A stale server.json does not bypass Dock's OS-level folder lock:
    # if a service is already running but nonresponsive, this raises,
    # rather than starting a second writer.
    # The manually opened window owns the detector. USB arrival never
    # opens another window and no watcher survives the UI lifetime.
    serve(Dock(data),0,True,False,True,ui_lifecycle=True)
    return 'started'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Abrir manualmente PSP Dock.')
    parser.add_argument('--data',type=Path,help='Biblioteca LIVE existente del PC')
    args=parser.parse_args()
    try:
        print('PSP Dock: iniciar o abrir la ventana XMB existente.',flush=True)
        print('Resultado:',launch(args.data),flush=True)
    except (OSError,ValueError) as exc:
        print('PSP Dock no se inició:',exc,flush=True)
        raise SystemExit(1)
