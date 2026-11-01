"""Conservative installer for the USB Bus Cycle 0.8 diagnostic.

This COPIES the two files, but never executes the experiment. The experiment
starts/stops USBBusDriver only after a deliberate L+R+X gesture, when its
preflight guards show a disconnected, inactive USB and stopped bus/storage.
No USBStor activation, no games/saves transfer, and no flash writes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dock import ROOT, inside, windows_devices
from install_safe_probe import digest
from install_usb_import_gate import install_package
from lab_eject import ppsspp_open, real_dock_open
from psp_usb import identify, UsbTransitionError


DEST = "PSP/GAME/PSPDockBusCycle08"
PACKAGE = {
    "EBOOT.PBP": (
        "homebrew/usb-bus-cycle-v08/dist/EBOOT.PBP",
        "2d53b122e2ab72039f73c7c34e29e3e0564d80560cf8ba9843b43c8f4ca0cac8",
    ),
    "usbstate.prx": (
        "homebrew/usb-bus-cycle-v08/dist/usbstate.prx",
        "012d994bb123428a10bc78fc1112f8a0567ec7714cf7547253ba5be9e29ac4b1",
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
            raise ValueError("USB Bus Test 0.8 ya existe: no se sobrescribirá.")
        # The original gate must have already been tested in this console.
        old = inside(root, "PSP/GAME/PSPDockUsbStatus07/EBOOT.PBP")
        if not old.is_file():
            raise ValueError("No está USB Driver Status 0.7 validado; no pasar a 0.8.")
        sources = {name: (ROOT / rel, sha) for name, (rel, sha) in PACKAGE.items()}
        for name, (source, sha) in sources.items():
            if not source.is_file() or digest(source) != sha:
                raise ValueError("Binario de 0.8 no verificado: " + name)
        print("PSP verificada:", identity.model, identity.drive)
        print("Versión anterior intacta:", old)
        print("NUEVA carpeta:", target)
        print("USB Bus Test 0.8 arranca y para USBBusDriver SOLO tras L+R+X.")
        print("El PRX bloquea la operacion con cable, USB activo o almacenamiento en uso.")
        print("NO inicia USBStor, NO monta Memory Stick, NO escribe partidas.")
        print("Conserva la version 0.7 anterior y el firmware intactos.")
        print("IMPORTANTE: el ciclo de bus aun NO se ha probado en PSP real.")
        if input("Escribe PREPARAR 08 para instalar (otra cosa cancela): ").strip().upper() != "PREPARAR 08":
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
