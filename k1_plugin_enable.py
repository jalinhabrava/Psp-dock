"""First guarded test: change K1 from OFF to ON without touching other plugins.

K1 is an isolated read-only kernel observer. This gate checks an exact Sony
PSP USB identity, critical backup of the OFF configuration, K1 SHA-256,
original VSH integrity and ARM absent. It never initiates USB role changes.
The recovery path is k1_plugin_recovery.py or START at PSP cold boot.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import uuid

from dock import inside, windows_devices
from install_safe_probe import digest
from lab_eject import ppsspp_open, real_dock_open
from psp_backup import DEFAULT_DEST, BackupError
from psp_usb import identify, UsbTransitionError
from vsh_plugin_enable import validated_backup
from vsh_plugin_status import inspect_volume
from k1_plugin_recovery import K1_ON_LINE, K1_OFF_LINE, plan_off_k1
from k1_plugin_stage import (
    K1StageError, K1_FILE, K1_SHA256, K1_STAGE_DIR, CONFIG,
    CONFIG_BACKUP_DIR, _VSH_LINE,
)


def plan_enable_k1(original: bytes) -> bytes:
    after_off, changed = plan_off_k1(original)
    if changed or after_off != original:
        raise K1StageError("K1 ya está ON: no activar de nuevo.")
    if original.replace(b"\r", b"").lower().splitlines().count(_VSH_LINE) != 1:
        raise K1StageError("El observador VSH ON no coincide.")
    lines = original.splitlines(keepends=True)
    result = []
    replaced = False
    for line in lines:
        if line.strip().lower() == K1_OFF_LINE.lower():
            newline = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
            result.append(K1_ON_LINE + newline)
            replaced = True
        else:
            result.append(line)
    after = b"".join(result)
    if not replaced or after == original or plan_off_k1(after) != (original, True):
        raise K1StageError("Cambio K1 OFF->ON no es reversible.")
    return after


def preflight_enable_k1(root: Path, backup_root: Path,
                        serial: str, usb_parent: str) -> dict:
    cfg = inside(root, CONFIG)
    if not cfg.is_file():
        raise K1StageError("No existe PLUGINS.TXT.")
    before = cfg.read_bytes()
    after = plan_enable_k1(before)
    binary = inside(root, K1_STAGE_DIR + "/" + K1_FILE)
    if not binary.is_file() or digest(binary) != K1_SHA256:
        raise K1StageError("El PRX K1 instalado no coincide con el artefacto validado.")
    vsh = inspect_volume(root)
    if (vsh["plugin_config"] != "ON" or
        not all(vsh["known_prx_integrity"].values()) or
        inside(root, "SEPLUGINS/PSPDockProbe/ARM.TXT").exists()):
        raise K1StageError("El observador VSH debe seguir ON, íntegro y sin ARM.")
    snapshot = validated_backup(backup_root, serial, usb_parent, before)
    return {"config": cfg, "before": before, "after": after, "backup": snapshot,
            "old_hash": hashlib.sha256(before).hexdigest(),
            "new_hash": hashlib.sha256(after).hexdigest()}


def enable_k1(root: Path, backup_root: Path, config_backups: Path,
              serial: str, usb_parent: str) -> dict:
    plan = preflight_enable_k1(root, backup_root, serial, usb_parent)
    config_backups.mkdir(parents=True, exist_ok=True)
    backup = config_backups / (
        "PLUGINS.before-k1-on-" + plan["old_hash"][:20] + ".bak"
    )
    if backup.exists():
        if backup.read_bytes() != plan["before"]:
            raise K1StageError("Backup existente K1 OFF no coincide.")
    else:
        with backup.open("xb") as writer:
            writer.write(plan["before"])
            writer.flush()
            os.fsync(writer.fileno())
    cfg = plan["config"]
    if cfg.read_bytes() != plan["before"]:
        raise K1StageError("PLUGINS.TXT cambió antes de activar K1.")
    temp = cfg.parent / (".k1-on-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as writer:
            writer.write(plan["after"])
            writer.flush()
            os.fsync(writer.fileno())
        if temp.read_bytes() != plan["after"] or cfg.read_bytes() != plan["before"]:
            raise K1StageError("Cambio inesperado antes de habilitar K1.")
        os.replace(temp, cfg)
    finally:
        if temp.exists():
            temp.unlink()
    if cfg.read_bytes() != plan["after"]:
        raise K1StageError("K1 ON no coincide; utilizar recuperación OFF.")
    return {"enabled": True, "k1": "ON", "source_backup": str(plan["backup"]),
            "config_backup": str(backup), "config_hash": plan["new_hash"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="PSP K1: ON solo primer boot")
    parser.add_argument("--run", action="store_true",
                        help="Confirmación exacta: habilitar kernel K1 pasivo")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise K1StageError("Cerrar PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise K1StageError("Conecta exactamente una PSP Sony por USB nativo.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        plan = preflight_enable_k1(root, DEFAULT_DEST, identity.volume_serial,
                                   identity.usb_device_id)
        print("PSP:", identity.drive, identity.model)
        print("Backup válido:", plan["backup"])
        print("K1 pasará OFF -> ON con una sola línea en PLUGINS.TXT.")
        print("K1 no modifica USB, firmware ni FAT; primer arranque kernel NO probado.")
        if not args.run:
            print("VISTA PREVIA; NO SE HA MODIFICADO LA PSP.")
            return 0
        if input("Escribe K1 KERNEL ON para ensayar el próximo boot: ").strip() != "K1 KERNEL ON":
            print("Cancelado.")
            return 0
        if identify(str(root))[0] != identity:
            raise K1StageError("PSP cambió antes de la activación.")
        print("ACTIVADO:", enable_k1(
            root, DEFAULT_DEST, CONFIG_BACKUP_DIR, identity.volume_serial,
            identity.usb_device_id))
        return 0
    except (K1StageError, BackupError, ValueError, OSError, UsbTransitionError) as exc:
        print("K1 NO ACTIVADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
