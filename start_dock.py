"""Launch or reconnect to the *same* PSP Dock LIVE instance on Windows.

Only one server can hold the LIVE folder's existing OS lock. The launcher
reads a host-local server record, verifies it with an HTTP health check,
and opens the XMB in an app-style window. Never launches a game or
changes USB modes simply because PSP has connected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from dock import (
    Dock, default_data_path, launch_xmb_window,
    service_fingerprint, serve, read_json,
)


def existing_server(data: Path, *, opener=None) -> str | None:
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
            result.get('data_fingerprint')==fingerprint):
            return url
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        pass
    return None


def launch(data: Path | None = None) -> str:
    if data is None:
        data=default_data_path(os.environ.get('LOCALAPPDATA',str(Path(__file__).parent)))
    data=Path(data).resolve()
    current=existing_server(data)
    if current:
        launch_xmb_window(current,app_window=True)
        return 'already_running'
    # A stale server.json does not bypass Dock's OS-level folder lock:
    # if a service is already running but nonresponsive, this raises,
    # rather than starting a second writer.
    # A device-triggered launch can remain open until the operator exits.
    # If the same PSP is connected again in that process lifetime, reuse
    # the XMB window on the verified HID rising edge as well.
    serve(Dock(data),0,True,True,True)
    return 'started'


if __name__=='__main__':
    try:
        print('PSP Dock: iniciar o abrir la ventana XMB existente.',flush=True)
        print('Resultado:',launch(),flush=True)
    except (OSError,ValueError) as exc:
        print('PSP Dock no se inició:',exc,flush=True)
        raise SystemExit(1)
