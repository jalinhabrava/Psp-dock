"""Read-only Windows USB transport observations for PSP Dock.

This is NOT a role switcher and cannot grant FAT ownership. It collects
independent evidence from an identified Sony PSP USB storage volume,
Windows PnP device statuses, and the existing *read-only* PDK1 HID probe.

Important: PnP removal, missing storage or a HID device alone never proves
firmware ownership or permission to copy SAVEDATA. A verified live role
protocol will be needed before any USBSTOR <-> HID automation.

Run with --samples N for a finite, non-mutating diagnostic. No eject,
DeviceIoControl, driver reset, USB writes, or PSP filesystem writes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import time
from typing import Any

from psp_dock.devices.bridge_hid import probe as hid_probe
from dock import windows_devices
from psp_dock.devices.psp_usb import identify, UsbTransitionError


class ObservationError(ValueError):
    pass


@dataclass(frozen=True)
class UsbObservation:
    time_utc: str
    state: str
    volume_serial: str | None
    drive: str | None
    usb_parent: str | None
    hid_candidates: int
    pnp_nodes: list[dict[str, Any]]
    warnings: list[str]
    role_transition_confirmed: bool = False
    exclusive_fat_confirmed: bool = False
    live_firmware_ack: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SONY_PARENT = re.compile(r"^USB\\VID_054C&PID_(?:02D2|01CB)(?:[&\\]|$)", re.I)
_SONY_DISK = re.compile(r'^USBSTOR\\DISK&VEN_SONY&PROD_.*PSP', re.I)


def select_sony_pnp_nodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter raw Windows PnP list, never attribute unrelated Sony products."""
    result = []
    for row in rows:
        ident = row.get("PNPDeviceID")
        if not isinstance(ident, str):
            continue
        if not _SONY_PARENT.match(ident) and not _SONY_DISK.match(ident):
            continue
        code = row.get("ConfigManagerErrorCode")
        if type(code) is not int or code < 0:
            code = None
        result.append({
            "pnp_id": ident,
            "name": str(row.get("Name") or ""),
            "status": str(row.get("Status") or ""),
            "problem_code": code,
        })
    return sorted(result, key=lambda item: item["pnp_id"].casefold())


_PNP_SCRIPT = r"""
$ErrorActionPreference='Stop'
$items=@(Get-CimInstance -ClassName Win32_PnPEntity |
  Where-Object {
    $_.PNPDeviceID -match '^USB\\VID_054C&PID_(02D2|01CB)' -or
    $_.PNPDeviceID -match '^USBSTOR\\DISK&VEN_SONY&PROD_.*PSP'
  } |
  Select-Object PNPDeviceID,Name,Status,ConfigManagerErrorCode)
ConvertTo-Json -InputObject $items -Compress -Depth 4
"""


def windows_pnp_snapshot() -> list[dict[str, Any]]:
    if os.name != "nt":
        raise ObservationError("Consulta PnP limitada a Windows.")
    process = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PNP_SCRIPT],
        capture_output=True, text=True, timeout=30, creationflags=0x08000000,
    )
    if process.returncode != 0:
        raise ObservationError(
            "No se pudo consultar PnP: " + (process.stderr.strip()[:150] or "error Windows")
        )
    try:
        payload = json.loads(process.stdout)
    except ValueError as exc:
        raise ObservationError("Windows devolvió una respuesta PnP inválida.") from exc
    if not isinstance(payload, list):
        raise ObservationError("La lista PnP tiene un formato inesperado.")
    return select_sony_pnp_nodes(payload)


def classify_observation(
    *, storage: dict | None, hid_devices: list[dict], pnp_nodes: list[dict],
    time_utc: str = "2000-01-01T00:00:00+00:00",
) -> UsbObservation:
    """Pure classification; all outcomes are observational, not ACKs."""
    hid_count = len(hid_devices)
    if storage is not None and hid_count:
        state = "DUAL_PRESENT_UNSAFE"
    elif storage is not None:
        state = "STORAGE_VISIBLE"
    elif hid_count:
        state = "HID_VISIBLE_UNBOUND"
    elif any(n.get("problem_code") == 47 for n in pnp_nodes):
        state = "WINDOWS_PREPARED_REMOVAL"
    else:
        state = "NO_PSP_INTERFACE_OBSERVED"

    warnings = ["Un estado Windows NO demuestra exclusividad FAT ni ACK PSP."]
    if any(n.get("problem_code") == 47 for n in pnp_nodes):
        warnings.append(
            "PnP código 47 detectado: posible dispositivo pendiente de "
            "desconexión física; NO intentar el puente automático."
        )
    if state == "DUAL_PRESENT_UNSAFE":
        warnings.append("Storage y HID coexisten: prohibido asumir transición.")
    if state == "WINDOWS_PREPARED_REMOVAL":
        warnings.append(
            "PnP código 47: Windows preparó retirada; puede requerir "
            "desconectar físicamente antes de volver a enumerar."
        )
    if state == "HID_VISIBLE_UNBOUND":
        warnings.append(
            "El HID PDK1 no está vinculado al serial anterior sin una "
            "prueba de misma PSP y sesión del gestor kernel."
        )
    if state == "NO_PSP_INTERFACE_OBSERVED":
        warnings.append("La ausencia de dispositivo no implica liberación de FAT.")
    return UsbObservation(
        time_utc=time_utc,
        state=state,
        volume_serial=storage["volume_serial"] if storage else None,
        drive=storage["drive"] if storage else None,
        usb_parent=storage["usb_device_id"] if storage else None,
        hid_candidates=hid_count,
        pnp_nodes=pnp_nodes,
        warnings=warnings,
    )


def sample_once() -> UsbObservation:
    """Read-only capture; identifies Sony PSP instead of trusting a drive letter."""
    devices = windows_devices()
    if len(devices) > 1:
        raise ObservationError("Hay varias unidades PSP candidatas: no atribuir identidad.")
    storage = None
    if devices:
        try:
            storage = asdict(identify(devices[0]["root"])[0])
        except (OSError, UsbTransitionError) as exc:
            raise ObservationError("Unidad PSP candidata sin identidad Sony verificada.") from exc

    report = hid_probe()
    if not report.get("supported"):
        raise ObservationError("Lectura de HID no disponible.")
    if report.get("errors"):
        raise ObservationError("El sondeo HID devolvió candidatos no verificables.")
    hid_devices = report.get("devices")
    if not isinstance(hid_devices, list):
        raise ObservationError("Datos HID desconocidos.")
    pnp = windows_pnp_snapshot()
    return classify_observation(
        storage=storage, hid_devices=hid_devices, pnp_nodes=pnp,
        time_utc=datetime.now(timezone.utc).isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="PSP Dock: observador USB Windows SOLO LECTURA")
    parser.add_argument("--samples", type=int, default=1,
                        help="Número finito de observaciones (1-60)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Segundos entre muestras (0.5-30)")
    args = parser.parse_args()
    if not (1 <= args.samples <= 60 and 0.5 <= args.interval <= 30):
        parser.error("Límites: muestras 1-60; intervalo 0.5-30 segundos.")
    try:
        for i in range(args.samples):
            if i:
                time.sleep(args.interval)
            print(json.dumps(sample_once().to_dict(), ensure_ascii=False))
        print("SOLO DIAGNÓSTICO. No se solicitó expulsión, ni cambio USB/HID.")
        return 0
    except (ObservationError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
