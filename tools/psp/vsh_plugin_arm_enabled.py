"""Later VSH runtime proof after first successful ON boot (not now).

Creates only the exact ARM marker under PSPDockProbe, while the registered
VSH plugin is ON, after independent verification:
* Sony USB identity and same verified critical backup of the OFF config.
* Current config is the ONE allowed OFF->ON change from the backed version.
* Both staged PRX hashes match the frozen reviewed binaries.
* No previous ARM or BOOT marker; the next boot should be disconnected.

It never enables plugins, launches firmware, or switches USB; the installed
observer can write BOOT.TXT only on a subsequent VSH boot *without cable*.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dock import inside, windows_devices
from tools.psp.install_safe_probe import digest
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from tools.psp.psp_backup import DEFAULT_DEST, BackupError
from psp_dock.devices.psp_usb import UsbTransitionError, identify
from tools.psp.vsh_plugin_enable import EnableError, plan_enable, validated_backup
from tools.psp.vsh_plugin_recovery import plan_off
from tools.psp.vsh_plugin_stage import ARM_TOKEN, CONFIG, FILES, STAGE_DIR


def plan_armed_on(root: Path, backups: Path, serial: str, usb_parent: str) -> dict:
    config = inside(root, CONFIG)
    if not config.is_file():
        raise EnableError("Configuración VSH no encontrada.")
    current = config.read_bytes()
    original_off, changed = plan_off(current)
    if not changed or plan_enable(original_off) != current:
        raise EnableError("No hay una activación única PSPDockProbe ON conocida.")
    snapshot = validated_backup(backups, serial, usb_parent, original_off)
    folder = inside(root, STAGE_DIR)
    for name, (_, checksum) in FILES.items():
        prx = folder / name
        if not prx.is_file() or digest(prx) != checksum:
            raise EnableError("El PRX instalado difiere del artefacto verificado: " + name)
    if (folder / "ARM.TXT").exists() or (folder / "BOOT.TXT").exists():
        raise EnableError("Ya existe ARM o BOOT; no sobrescribir ni rearmar.")
    return {"folder": folder, "config": config, "config_bytes": current,
            "backup": snapshot}


def arm_for_next_boot(root: Path, backups: Path, serial: str, usb_parent: str) -> dict:
    plan = plan_armed_on(root, backups, serial, usb_parent)
    if plan["config"].read_bytes() != plan["config_bytes"]:
        raise EnableError("PLUGINS.TXT cambió durante el análisis.")
    marker = plan["folder"] / "ARM.TXT"
    with marker.open("xb") as output:
        output.write(ARM_TOKEN)
        output.flush()
        os.fsync(output.fileno())
    if marker.read_bytes() != ARM_TOKEN:
        raise EnableError("No se pudo verificar ARM.TXT; no iniciar el plugin.")
    return {"armed": True, "plugin_config": "ON",
            "marker": str(marker), "backup": str(plan["backup"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Preparar prueba VSH ARM tras primer boot ON")
    parser.add_argument("--run", action="store_true",
                        help="Confirmación exacta para crear ARM.TXT")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise EnableError("Cerrar PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise EnableError("No hay exactamente una PSP Sony conectada por USB.")
        root = Path(devices[0]["root"])
        original, _ = identify(str(root))
        plan = plan_armed_on(root, DEFAULT_DEST, original.volume_serial,
                             original.usb_device_id)
        print("Observador VSH ON, sin ARM, backup comprobado:", plan["backup"])
        print("CREAR ARM.TXT hará que el PROXIMO arranque lea USB y escriba BOOT.TXT.")
        print("No activar con cable conectado. No cambia USB ni firmware.")
        if not args.run:
            print("VISTA PREVIA — no se escribió nada.")
            return 0
        if input("Escribe ARM TEST VSH para autorizar la siguiente prueba: ").strip() != "ARM TEST VSH":
            print("Cancelado sin crear ARM.")
            return 0
        if identify(str(root))[0] != original:
            raise EnableError("PSP Sony cambió de identidad.")
        print(arm_for_next_boot(root, DEFAULT_DEST, original.volume_serial,
                                original.usb_device_id))
        return 0
    except (EnableError, BackupError, ValueError, OSError, UsbTransitionError) as exc:
        print("ARM TEST VSH CANCELADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
