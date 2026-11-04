"""Install ONLY a separate USER module-list diagnostic, never kernel drivers.

The EBOOT runs manually from XMB in development; it queries module names
and cannot switch USB roles. No existing folder, plugin or SAVEDATA is
overwritten. Every actual PSP write is gated by Sony USB identity, SHA256
and a verified pre-install critical backup of the current configuration.
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
from tools.psp.vsh_plugin_status import inspect_volume
from tools.psp.k1_plugin_enable import K1StageError, K1_ON_LINE
from tools.psp.k1_plugin_stage import (
    CONFIG, K1_FILE, K1_STAGE_DIR, K1_SHA256, _VSH_LINE
)


CHECK_DIR = "PSP/GAME/PSPDockK1Check"
EBOOT = ROOT / "homebrew/k1-module-check/dist/EBOOT.PBP"
EBOOT_HASH = "84267800c1c2c807884134afc2b974513830d78d89b60435fa92582542b903dc"


def inspect_install(root: Path, backups: Path, serial: str,
                    usb_parent: str, source: Path = EBOOT) -> dict:
    if not source.is_file() or digest(source) != EBOOT_HASH:
        raise K1StageError("EBOOT K1Check no coincide con artefacto USER revisado.")
    dest = inside(root, CHECK_DIR)
    if dest.exists():
        raise K1StageError("K1Check ya existe; no sobreescribir.")
    cfg = inside(root, CONFIG)
    if not cfg.is_file():
        raise K1StageError("No existe PLUGINS.TXT.")
    config = cfg.read_bytes()
    lines = config.replace(b"\r", b"").lower().splitlines()
    if lines.count(_VSH_LINE) != 1 or lines.count(K1_ON_LINE.lower()) != 1:
        raise K1StageError("La configuración VSH/K1 ON ha cambiado.")
    vsh = inspect_volume(root)
    if (vsh["plugin_config"] != "ON"
        or not all(vsh["known_prx_integrity"].values())
        or inside(root, "SEPLUGINS/PSPDockProbe/ARM.TXT").exists()):
        raise K1StageError("Observador VSH no está íntegro o desarmado.")
    k1 = inside(root, K1_STAGE_DIR + "/" + K1_FILE)
    if not k1.is_file() or digest(k1) != K1_SHA256:
        raise K1StageError("PRX K1 no corresponde al binario validado.")
    snapshot = validated_backup(backups, serial, usb_parent, config)
    if shutil.disk_usage(root).free < 8 * 1024 * 1024 + source.stat().st_size:
        raise K1StageError("Espacio PSP insuficiente.")
    return {"dest": dest, "source": source, "backup": snapshot, "config": cfg,
            "config_data": config}


def install_check(root: Path, backups: Path, serial: str,
                  usb_parent: str, source: Path = EBOOT) -> dict:
    plan = inspect_install(root, backups, serial, usb_parent, source)
    if plan["config"].read_bytes() != plan["config_data"]:
        raise K1StageError("Configuración PSP cambió antes de instalar.")
    temp = inside(root, "PSP/GAME/.pspdock-k1-check-" + uuid.uuid4().hex)
    temp.mkdir(parents=True, exist_ok=False)
    try:
        target = temp / "EBOOT.PBP"
        with source.open("rb") as inp, target.open("xb") as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        if digest(target) != EBOOT_HASH:
            raise K1StageError("EBOOT copiado no supera SHA256.")
        if plan["dest"].exists() or plan["config"].read_bytes() != plan["config_data"]:
            raise K1StageError("Destino o plugins cambiaron durante la copia.")
        os.rename(temp, plan["dest"])
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    if digest(plan["dest"] / "EBOOT.PBP") != EBOOT_HASH:
        raise K1StageError("Instalación K1Check no verificable.")
    return {"installed": True, "autostart": False, "path": str(plan["dest"]),
            "sha256": EBOOT_HASH, "source_backup": str(plan["backup"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Instalar K1Check USER sin autoarranque")
    parser.add_argument("--install", action="store_true",
                        help="Copia el homebrew con confirmación explícita")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise K1StageError("Cerrar PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise K1StageError("Se requiere una única PSP Sony por USB.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        plan = inspect_install(root, DEFAULT_DEST, identity.volume_serial,
                               identity.usb_device_id)
        print("PSP Sony:", identity.drive, identity.model)
        print("Backup antes de instalar:", plan["backup"])
        print("Nueva carpeta USER:", plan["dest"])
        print("EBOOT SHA256:", EBOOT_HASH)
        print("Solo consulta lista de módulos; no cambia USB ni autoarranque.")
        if not args.install:
            print("PREVIEW: sin cambios.")
            return 0
        if input("Escribe INSTALAR K1 CHECK: ").strip() != "INSTALAR K1 CHECK":
            print("Cancelado sin copiar.")
            return 0
        if identify(str(root))[0] != identity:
            raise K1StageError("La identidad Sony ha cambiado.")
        print("INSTALADO:", install_check(
            root, DEFAULT_DEST, identity.volume_serial, identity.usb_device_id))
        return 0
    except (K1StageError, BackupError, ValueError, OSError, UsbTransitionError) as exc:
        print("K1Check NO INSTALADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
