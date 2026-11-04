"""Install PSP Dock's non-autostart startup diagnostics, never the old probe.

Default mode is screen-only: no USB calls in that EBOOT. Installs in a new
PSP/GAME folder and refuses to overwrite any existing content. Real hardware
Sony PSP identification is mandatory to avoid touching a different USB drive.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import uuid

from dock import ROOT, inside, windows_devices
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from psp_dock.devices.psp_usb import UsbTransitionError, identify


PROBES = {
    "screen": {
        "source": "homebrew/screen-check/dist/EBOOT.PBP",
        "destination": "PSP/GAME/PSPDockScreenSafe",
        "sha256": "ebdcbee2e624896dcdf2892f97e4ecb37e21d7b11e7f51f9109b8c693227a35e",
    },
    "usb-steps": {
        "source": "homebrew/usb-steps/dist/EBOOT.PBP",
        "destination": "PSP/GAME/PSPDockUsbSteps",
        "sha256": "36be82b7589f38b1e0e3e3134909f699e7c3da4c81257adeee832818da7035f9",
    },
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def destination_for(root: Path, mode: str) -> Path:
    if mode not in PROBES:
        raise ValueError("Modo de instalación desconocido.")
    target = inside(root, PROBES[mode]["destination"])
    if target.exists():
        raise ValueError("La carpeta de esta prueba ya existe. No se sobrescribe nada: " + str(target))
    return target


def stage_binary(root: Path, source: Path, mode: str) -> Path:
    """Testable pure-file operation; caller verifies the physical PSP first."""
    entry = PROBES[mode]
    if not source.is_file() or digest(source) != entry["sha256"]:
        raise ValueError("EBOOT ausente o distinto del binario verificado. Recompila y revisa el hash.")
    target = destination_for(root, mode)
    if shutil.disk_usage(root).free < source.stat().st_size + 8 * 1024 * 1024:
        raise ValueError("No queda espacio suficiente en la Memory Stick.")
    target.mkdir(parents=True, exist_ok=False)
    stage = target / (".EBOOT.partial-" + uuid.uuid4().hex)
    dest = target / "EBOOT.PBP"
    try:
        with source.open("rb") as reader, stage.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if digest(stage) != entry["sha256"]:
            raise ValueError("La verificación SHA-256 en PSP ha fallado.")
        os.replace(stage, dest)
        if digest(dest) != entry["sha256"]:
            raise ValueError("La copia final no coincide; conservar la carpeta para diagnóstico.")
        return dest
    finally:
        if stage.exists():
            stage.unlink()


def confirm_install(mode: str, *, yes: bool = False, read=input) -> bool:
    """Keep first (no-USB) diagnostic one-click; gate all USB operations."""
    if yes:
        if mode != "screen":
            raise ValueError("La instalación automática solo está permitida para la prueba SIN USB.")
        return True
    if mode == "screen":
        answer = read("Pulsa INTRO para instalar la prueba de pantalla (N para cancelar): ")
        return answer.strip().casefold() in ("", "s", "si", "sí")
    return read("Escribe PANTALLA OK si has comprobado la primera prueba: ").strip().upper() == "PANTALLA OK"


def main() -> int:
    parser = argparse.ArgumentParser(description="Instalador conservador PSP Dock SAFE")
    parser.add_argument("--mode", choices=tuple(PROBES), default="screen")
    parser.add_argument("--yes", action="store_true",
                        help="Instalación sin confirmación SOLO para la prueba de pantalla sin USB")
    args = parser.parse_args()
    try:
        if args.mode == "usb-steps":
            raise ValueError(
                "USB Step Probe 1.0 está RETIRADO: provocó apagado en la PSP real. "
                "No vuelvas a instalarlo ni ejecutarlo.")
        if ppsspp_open() or real_dock_open():
            raise ValueError("Cierra PPSSPP y el PSP Dock real antes de instalar pruebas.")
        candidates = windows_devices()
        if len(candidates) != 1:
            raise ValueError("Conecta exactamente una PSP real en modo USB. No hay una unidad única.")
        root = Path(candidates[0]["root"])
        identity, _ = identify(str(root))
        entry = PROBES[args.mode]
        source = ROOT / entry["source"]
        dest = destination_for(root, args.mode)
        print("PSP detectada:", identity.model, identity.drive)
        print("Tipo de prueba:", args.mode)
        print("Origen:", source)
        print("Destino NUEVO:", dest)
        if args.mode == "usb-steps":
            print("ATENCIÓN: este segundo EBOOT ejecuta llamadas USB bajo controles manuales.")
            print("No lo instales sin haber ejecutado con éxito Startup Check SAFE.")
        else:
            print("La prueba inicial NO cambia el modo USB ni accede a tus partidas.")
        if not confirm_install(args.mode, yes=args.yes):
            print("Operación cancelada sin escribir en la PSP.")
            return 0
        # Recheck after the confirmation to guard against unplug/replug.
        rechecked, _ = identify(str(root))
        if identity != rechecked:
            raise ValueError("La PSP cambió durante el diálogo; se cancela.")
        result = stage_binary(root, source, args.mode)
        print("Copia verificada:", result)
        print("Expulsa la PSP desde Windows antes de desconectar el cable.")
        print("Los guardados y la antigua carpeta PSPDockProbe no se han tocado.")
        return 0
    except (ValueError, UsbTransitionError, OSError) as exc:
        print("INSTALACIÓN CANCELADA:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
