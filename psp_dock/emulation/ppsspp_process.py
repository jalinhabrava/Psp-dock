"""Read-only Windows process checks shared by the Dock and USB diagnostics."""

from __future__ import annotations

import subprocess

from psp_dock.devices.psp_usb import UsbTransitionError


def real_dock_open() -> bool:
    """Fail closed if a live Dock service could be syncing this same volume."""
    script = r'''
$ErrorActionPreference="Stop"
@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and
    $_.CommandLine -match 'dock\.py' -and
    $_.CommandLine -notmatch '\-\-demo'
}).Count
'''
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        text=True, capture_output=True, timeout=30, creationflags=0x08000000)
    if result.returncode or not result.stdout.strip().isdigit():
        raise UsbTransitionError("No se pudo descartar un Dock real escribiendo en USB.")
    return int(result.stdout.strip()) != 0


def ppsspp_open() -> bool:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         '@(Get-Process PPSSPPWindows,PPSSPPWindows64 -ErrorAction SilentlyContinue).Count'],
        text=True, capture_output=True, timeout=30, creationflags=0x08000000)
    if result.returncode or not result.stdout.strip().isdigit():
        raise UsbTransitionError("No se puede confirmar el cierre de PPSSPP.")
    return int(result.stdout.strip()) != 0
