"""Controlled PSP USBSTOR release experiment for the installed PSPDockProbe.

This lab program does NOT establish automatic mode switching. It asks for
explicit confirmation that the observer is running on the physical PSP,
checks identity, then requests *non-forced* safe removal from Windows.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import time

from psp_dock.devices.bridge_hid import probe as hid_probe
from dock import ROOT, windows_devices
from psp_dock.devices.psp_usb import UsbTransitionError, identify, safe_eject
from psp_dock.emulation.ppsspp_process import ppsspp_open, real_dock_open


def run() -> int:
    raise UsbTransitionError(
        "SUSPENDIDO: ambos diagnósticos USB provocaron apagado antes de "
        "mostrar pantalla en la PSP real. No realizar expulsión de laboratorio "
        "hasta validar importaciones y un homebrew USB operativo.")
    if ppsspp_open():
        raise UsbTransitionError("Cierra PPSSPP antes del ensayo USB.")
    if real_dock_open():
        raise UsbTransitionError("Cierra el servicio PSP Dock real antes de expulsar el USB.")
    devices = windows_devices()
    if len(devices) != 1:
        raise UsbTransitionError(
            f"Se exige exactamente una PSP montada; hay {len(devices)} candidatas.")
    source = devices[0]["root"]
    storage, _ = identify(source)
    print("PSP:", storage.model, storage.drive)
    print("USB:", storage.usb_device_id)
    print("\nAbre JUEGOS > Memory Stick > PSP Dock USB Probe EN LA PSP.")
    print("Espera a que muestre USBSTOR ACTIVE; confirma que no haya copias abiertas.")
    print("Comprueba cambios del estado USB en la pantalla PSP tras el ensayo.")
    print("NO fuerces extracción ni desconectes durante transferencias.")
    if input("\nEscribe EXPULSAR únicamente si el probe está activo: ").strip() != "EXPULSAR":
        print("Cancelado sin solicitar expulsión.")
        return 0
    if ppsspp_open() or real_dock_open():
        raise UsbTransitionError("PPSSPP o el Dock real se han abierto; ensayo cancelado.")
    if identify(source)[0] != storage:
        raise UsbTransitionError("Dispositivo modificado durante la preparación.")
    outcome = safe_eject(source, storage.volume_serial)
    observations = []
    if outcome["host_eject_accepted"]:
        for tick in range(12):
            observations.append({
                "seconds": tick, "hid": hid_probe(),
                "drive_visible": Path(source).is_dir()})
            time.sleep(1)
    report = {
        "experiment": "host_safe_eject_psp_probe",
        "time": datetime.now().isoformat(),
        "storage": {
            "drive": storage.drive,
            "volume_serial": storage.volume_serial,
            "usb_device_id": storage.usb_device_id,
        },
        "host": outcome, "observations": observations,
        "psp_screen": "Leer en la PSP; el host no infiere eventos de RAM.",
    }
    path = ROOT / "docs" / ("probe-eject-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nRegistro guardado:", path)
    if not outcome["host_eject_accepted"]:
        print("Windows vetó la expulsión; no se forzó.", outcome["veto_name"])
        return 2
    print("Host confirmó expulsión. Todavía NO confirma transición a HID ni propiedad FAT.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (UsbTransitionError, OSError, subprocess.SubprocessError) as exc:
        print("PRUEBA CANCELADA:", exc)
        raise SystemExit(1)
