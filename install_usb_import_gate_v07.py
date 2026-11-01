"""Conservative installer for USB Driver Status 0.7 (never overwrites 0.6).

No direct USB kernel drivers are started or stopped. This program copies two
locally verified PSP files to a NEW directory on a verified Sony PSP volume.
Run the EBOOT from XMB only after normal Windows USB safe removal.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dock import ROOT, inside, windows_devices
from install_safe_probe import digest
from install_usb_import_gate import install_package
from lab_eject import ppsspp_open, real_dock_open
from psp_usb import identify, UsbTransitionError


DEST = "PSP/GAME/PSPDockUsbStatus07"
PACKAGE = {
    "EBOOT.PBP": (
        "homebrew/usb-driver-status-v07/dist/EBOOT.PBP",
        "0b45940b6281884825dbf7b0dc93a0ad445d8e6b6b052f072b50b5eb390c3cc1",
    ),
    "usbstate.prx": (
        "homebrew/usb-driver-status-v07/dist/usbstate.prx",
        "dcb2a33e7418a8f4d48d93485c9d53db2173e0bf059a857caf8f440d80dff457",
    ),
}


def main() -> int:
    try:
        if ppsspp_open() or real_dock_open():
            raise ValueError("Cierra PPSSPP y PSP Dock real antes de instalar pruebas.")
        devices = windows_devices()
        if len(devices) != 1:
            raise ValueError("Conecta una sola PSP Sony en modo USB nativo del XMB.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        target = inside(root, DEST)
        if target.exists():
            raise ValueError("USB Driver Status 0.7 ya existe: no se sobrescribirá.")
        # The original gate must have already been tested in this console.
        old = inside(root, "PSP/GAME/PSPDockUsbGate06/EBOOT.PBP")
        if not old.is_file():
            raise ValueError("No está el Import Gate 0.6 validado; no pasar a 0.7.")
        sources = {name: (ROOT / rel, sha) for name, (rel, sha) in PACKAGE.items()}
        for name, (source, sha) in sources.items():
            if not source.is_file() or digest(source) != sha:
                raise ValueError("Binario de 0.7 no verificado: " + name)
        print("PSP verificada:", identity.model, identity.drive)
        print("Versión anterior intacta:", old)
        print("NUEVA carpeta:", target)
        print("USB Driver Status 0.7 solo consulta estados de controladores USB.")
        print("No inicia, detiene ni activa USB y no escribe partidas.")
        print("Conserva Import Gate 0.6 y el firmware intactos.")
        print("IMPORTANTE: 0.7 no se ha probado en PSP real.")
        if input("Escribe PREPARAR 07 para instalar (otra cosa cancela): ").strip().upper() != "PREPARAR 07":
            print("Cancelado sin escribir.")
            return 0
        rechecked, _ = identify(str(root))
        if rechecked != identity:
            raise ValueError("La identidad de PSP cambió antes de copiar.")
        path = install_package(root, sources, relative_dest=DEST)
        print("Archivos y SHA-256 verificados en:", path)
        print("Usa expulsión segura NORMAL de Windows antes de salir de USB.")
        return 0
    except (ValueError, OSError, UsbTransitionError) as exc:
        print("INSTALACIÓN CANCELADA:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
