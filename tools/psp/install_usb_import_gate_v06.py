"""Conservative installer for USB Import Gate 0.6 (never overwrites 0.5).

No direct USB kernel drivers are started or stopped. This program copies two
locally verified PSP files to a NEW directory on a verified Sony PSP volume.
Run the EBOOT from XMB only after normal Windows USB safe removal.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dock import ROOT, inside, windows_devices
from tools.psp.install_safe_probe import digest
from tools.psp.install_usb_import_gate import install_package
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from psp_dock.devices.psp_usb import identify, UsbTransitionError


DEST = "PSP/GAME/PSPDockUsbGate06"
PACKAGE = {
    "EBOOT.PBP": (
        "homebrew/usb-import-gate-v06/dist/EBOOT.PBP",
        "e183d8c95aaf5bdbf1992c5667e0ffeece781cfb15f656ad6729d6b5d41d7b21",
    ),
    "usbstate.prx": (
        "homebrew/usb-import-gate-v06/dist/usbstate.prx",
        "358554a1c490be4f3bd08c8d8ea542b0ec187609ecee0b51973312b488a40a9b",
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
            raise ValueError("Import Gate 0.6 ya existe: no se sobrescribirá.")
        # The original gate must have already been tested in this console.
        old = inside(root, "PSP/GAME/PSPDockUsbGate/EBOOT.PBP")
        if not old.is_file():
            raise ValueError("No está el Import Gate 0.5 verificado; no pasar a 0.6.")
        sources = {name: (ROOT / rel, sha) for name, (rel, sha) in PACKAGE.items()}
        for name, (source, sha) in sources.items():
            if not source.is_file() or digest(source) != sha:
                raise ValueError("Binario de 0.6 no verificado: " + name)
        print("PSP verificada:", identity.model, identity.drive)
        print("Versión anterior intacta:", old)
        print("NUEVA carpeta:", target)
        print("Import Gate 0.6 corrige el ciclo de vida del módulo.")
        print("No toca partidas, firmware ni el viejo Import Gate 0.5.")
        print("IMPORTANTE: no se ha probado aún en PSP real.")
        if input("Escribe PREPARAR 06 para instalar (otra cosa cancela): ").strip().upper() != "PREPARAR 06":
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
