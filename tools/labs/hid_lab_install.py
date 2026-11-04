"""Stage a manually launched HID-only laboratory on one verified Sony PSP.

No autorun, no PLUGINS.TXT changes, no USB eject, no driver operations.
All writes are restricted to a NEW PSP/GAME/PSPDockHidOnly directory.
The source USB storage is always owned by Windows during installation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import uuid

from dock import ROOT, inside, windows_devices
from tools.psp.install_safe_probe import digest
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from tools.psp.psp_backup import DEFAULT_DEST, BackupError
from psp_dock.devices.psp_usb import identify, UsbTransitionError
from tools.psp.vsh_plugin_enable import validated_backup
from tools.psp.vsh_plugin_stage import CONFIG


class HidLabInstallError(ValueError):
    pass


TARGET = "PSP/GAME/PSPDockHidOnly"
SOURCES = {
    "EBOOT.PBP": (
        "homebrew/hid-only-lab/dist/EBOOT.PBP",
        "e07549ca20546ecdd93f8258f8a4363dec434bfebbb4e2b5c502702442c7092e",
    ),
    "hidonlycore.prx": (
        "homebrew/hid-only-lab/dist/hidonlycore.prx",
        "06e0bcea46a09bf7310f58bc7c931098e427b7051769f03383d7f591dba1d1cf",
    ),
    "bridgehid.prx": (
        "homebrew/hid-only-lab/dist/bridgehid.prx",
        "8f702c70b8464101cc24c5661b0cff7ef327683b0896f094b42837296d2a548e",
    ),
    "LICENSE.txt": (
        "homebrew/bridge-hid/LICENSE",
        "230184f60bae2feaf244f10a8bac053c8ff33a183bcc365b4d8b876d2b7f4809",
    ),
}


def plan_install(root: Path, backups: Path, serial: str, usb_parent: str) -> dict:
    """Strict, read-only preview of a new folder on a backed-up PSP."""
    base = inside(root, TARGET)
    if base.exists():
        raise HidLabInstallError("Ya existe el destino HID LAB; no sobreescribir.")
    if not (root / "PSP/GAME").is_dir():
        raise HidLabInstallError("No hay estructura PSP/GAME reconocida.")
    config = inside(root, CONFIG)
    if not config.is_file():
        raise HidLabInstallError("No existe configuración CFW de PSP.")
    config_bytes = config.read_bytes()
    backup = validated_backup(backups, serial, usb_parent, config_bytes)
    checks = {}
    total = 0
    for filename, (relative, sha) in SOURCES.items():
        source = ROOT / relative
        if not source.is_file() or digest(source) != sha:
            raise HidLabInstallError("Fuente HID LAB no validada: " + filename)
        total += source.stat().st_size
        checks[filename] = (source, sha)
    if shutil.disk_usage(root).free < total + 8 * 1024 * 1024:
        raise HidLabInstallError("Espacio insuficiente en Memory Stick.")
    return {"target": base, "sources": checks, "backup": backup,
            "config": config, "config_bytes": config_bytes, "bytes": total}


def install_lab(root: Path, backups: Path, serial: str, usb_parent: str) -> dict:
    plan = plan_install(root, backups, serial, usb_parent)
    if plan["config"].read_bytes() != plan["config_bytes"]:
        raise HidLabInstallError("PLUGINS.TXT cambió durante la preparación.")
    tmp = inside(root, "PSP/GAME/.pspdock-hid-only-" + uuid.uuid4().hex)
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        for filename, (source, expected) in plan["sources"].items():
            candidate = tmp / filename
            with source.open("rb") as reader, candidate.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            if digest(candidate) != expected:
                raise HidLabInstallError("Copia en PSP incorrecta: " + filename)
        if plan["target"].exists() or plan["config"].read_bytes() != plan["config_bytes"]:
            raise HidLabInstallError("El destino o configuración CFW cambiaron.")
        os.rename(tmp, plan["target"])
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    for filename, (_, expected) in plan["sources"].items():
        if digest(plan["target"] / filename) != expected:
            raise HidLabInstallError("Verificación final falló: " + filename)
    if plan["config"].read_bytes() != plan["config_bytes"]:
        raise HidLabInstallError("La configuración CFW cambió tras instalar el laboratorio.")
    return {"installed": True, "autorun": False, "filesystem_writes_only": TARGET,
            "backup": str(plan["backup"]), "files": len(SOURCES),
            "bytes": plan["bytes"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Instalar PSP Dock HID-only LAB sin arranque automático")
    parser.add_argument("--install", action="store_true",
                        help="Copia únicamente a una carpeta nueva, con confirmación")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise HidLabInstallError("Cierra PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise HidLabInstallError("Se necesita exactamente una PSP Sony.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        plan = plan_install(root, DEFAULT_DEST, identity.volume_serial, identity.usb_device_id)
        print("PSP Sony:", identity.drive, identity.model)
        print("Backup de estado actual verificado:", plan["backup"])
        print("Destino nuevo:", plan["target"])
        print("Archivos:", len(SOURCES), "bytes:", plan["bytes"])
        print("Sin autoarranque. No altera drivers USB, plugin CFW o partidas.")
        if not args.install:
            print("PREVIEW: no se ha modificado PSP.")
            return 0
        if input("Escribe INSTALAR HID LAB para confirmar: ").strip() != "INSTALAR HID LAB":
            print("Cancelado sin escribir.")
            return 0
        if identify(str(root))[0] != identity:
            raise HidLabInstallError("Sony USB cambió antes de la copia.")
        print("RESULTADO:", install_lab(root, DEFAULT_DEST, identity.volume_serial,
                                        identity.usb_device_id))
        return 0
    except (HidLabInstallError, BackupError, ValueError,
            OSError, UsbTransitionError) as exc:
        print("HID LAB CANCELADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
