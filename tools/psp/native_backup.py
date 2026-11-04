"""Conservative, read-only backup guards for the final PSP Dock homebrew.

These helpers verify the actual Memory Stick against a PC backup before a
0308 install/upgrade. They don't implement or import legacy test firmware.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.psp.psp_backup import selection, verify_archive
from tools.psp.psp_native_package import digest


def no_links(path: Path) -> None:
    for item in (Path(path), *Path(path).parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('No se admiten enlaces en la ruta de instalación.')


def matching_backup(root: Path, snapshot: Path, serial: str, usb_id: str) -> dict:
    """Reject changed PSP content even if an old PC manifest still verifies."""
    root, snapshot = Path(root), Path(snapshot)
    verify_archive(snapshot)
    saved = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    if (saved.get('volume_serial') != serial or saved.get('usb_device_id') != usb_id or
            saved.get('scope') not in ('critical', 'full')):
        raise ValueError('El backup no corresponde a esta PSP y Memory Stick.')
    current, _ = selection(root, 'critical')
    if not current:
        raise ValueError('No se ha verificado el estado actual de la PSP.')
    if saved['scope'] == 'critical' and set(saved['files']) != {p for p, _ in current}:
        raise ValueError('El contenido crítico cambió desde el backup.')
    for relative, size in current:
        record = saved['files'].get(relative)
        if not record or record['size'] != size or digest(root / relative) != record['sha256']:
            raise ValueError('El backup no refleja el estado actual: ' + relative)
    return saved
