"""First reversible ARK-5 PSP Dock VSH boot test: OFF -> ON, WITHOUT ARM.

This is intentionally NOT the USB Dock implementation. It enables just the
already copied user-mode observer to see whether ARK can load it in VSH.
No ARM.TXT = no helper USB imports, no telemetry write and no role changes.

Gates:
* exact Sony PSP identity; exactly one removable volume and matching backup,
* verified critical backup containing current PSPDockProbe OFF config,
* exact staged PRX hashes and no ARM/BOOT marker,
* one known OFF config line, no conflicting PSP Dock entries,
* PPSSPP/real Dock closed, and no outstanding transfers supplied by caller.
* explicit on-disk backup of original PLUGINS.TXT in PC before single replace.

Only one byte range of PLUGINS.TXT (last field OFF -> ON) changes.
Never changes flash or SAVEDATA.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from dock import inside, windows_devices
from tools.psp.install_safe_probe import digest
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from tools.psp.psp_backup import DEFAULT_DEST, BackupError, verify_archive
from psp_dock.devices.psp_usb import UsbTransitionError, identify
from tools.psp.vsh_plugin_stage import CONFIG, ENTRY, FILES, STAGE_DIR
from tools.psp.vsh_plugin_recovery import BACKUPS as CONFIG_BACKUPS, plan_off


class EnableError(ValueError):
    pass


def validated_backup(backup_root: Path, serial: str, usb_parent: str,
                     config: bytes) -> Path:
    """Select a verified critical backup of THIS PSP, with matching config."""
    if not backup_root.is_dir():
        raise EnableError("No existe un respaldo PSP previo en el PC.")
    for snapshot in sorted((p for p in backup_root.iterdir() if p.is_dir()),
                           key=lambda p: p.name, reverse=True):
        manifest = snapshot / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            report = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            continue
        if (report.get("scope") not in ("critical", "full") or
            report.get("volume_serial") != serial or
            report.get("usb_device_id") != usb_parent):
            continue
        # Fail closed on corruption of candidate that appears to match.
        verify_archive(snapshot)
        backed_config = snapshot / "data" / CONFIG
        if not backed_config.is_file() or backed_config.read_bytes() != config:
            continue
        return snapshot
    raise EnableError("No existe backup crítico VERIFICADO de esta PSP y esta configuración.")


def plan_enable(original: bytes) -> bytes:
    """Only change the one exact line registered OFF; preserve others verbatim."""
    if len(original) > 128 * 1024 or b"\x00" in original:
        raise EnableError("PLUGINS.TXT inválido o demasiado grande.")
    lines = original.splitlines(keepends=True)
    matching = []
    for index, item in enumerate(lines):
        clean = item.strip().lower()
        if b"pspdockprobe" in clean or b"pspdockvsh.prx" in clean:
            matching.append((index, clean))
    if len(matching) != 1:
        raise EnableError("No hay una entrada única PSPDockProbe que activar.")
    index, actual = matching[0]
    expected = ENTRY.strip().lower()
    if actual != expected:
        raise EnableError("PSPDockProbe no está exactamente en OFF. No se modifica.")
    value = lines[index]
    eol = b"\r\n" if value.endswith(b"\r\n") else b"\n" if value.endswith(b"\n") else b""
    lines[index] = ENTRY.rstrip(b"\n").replace(b", off", b", on") + eol
    updated = b"".join(lines)
    # A subsequent recovery of the ON state must restore original bytes.
    restored, changed = plan_off(updated)
    if not changed or restored != original:
        raise EnableError("El plan de recuperación OFF no revierte exactamente el cambio.")
    return updated


def preflight(root: Path, backup_root: Path, serial: str,
              usb_parent: str) -> dict:
    """Does not modify any files."""
    cfg = inside(root, CONFIG)
    if not cfg.is_file():
        raise EnableError("PLUGINS.TXT PSP no existe.")
    original = cfg.read_bytes()
    proposed = plan_enable(original)
    folder = inside(root, STAGE_DIR)
    for filename, (_, checksum) in FILES.items():
        candidate = folder / filename
        if not candidate.is_file() or digest(candidate) != checksum:
            raise EnableError("El PRX instalado no coincide: " + filename)
    if (folder / "ARM.TXT").exists() or (folder / "BOOT.TXT").exists():
        raise EnableError("Observador armado o con registro anterior. Solo ensayar SIN ARM.")
    source_backup = validated_backup(backup_root, serial, usb_parent, original)
    return {
        "config": cfg,
        "original": original,
        "proposed": proposed,
        "verified_backup": source_backup,
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "updated_sha256": hashlib.sha256(proposed).hexdigest(),
    }


def enable_observer_off_to_on(root: Path, backup_root: Path,
                             config_backups: Path, serial: str,
                             usb_parent: str) -> dict:
    """Single atomic OFF->ON config change; no USB commands issued."""
    plan = preflight(root, backup_root, serial, usb_parent)
    original = plan["original"]
    proposed = plan["proposed"]
    cfg = plan["config"]
    config_backups.mkdir(parents=True, exist_ok=True)
    backup = config_backups / ("PLUGINS.before-vsh-on-" +
                               plan["original_sha256"][:20] + ".bak")
    if backup.exists():
        if backup.read_bytes() != original:
            raise EnableError("Backup local existente no coincide. Se cancela.")
    else:
        with backup.open("xb") as file:
            file.write(original)
            file.flush()
            os.fsync(file.fileno())
    if cfg.read_bytes() != original:
        raise EnableError("PLUGINS.TXT cambió durante el preflight.")
    staging = cfg.parent / (".pspdock-enable-" + uuid.uuid4().hex + ".tmp")
    try:
        with staging.open("xb") as output:
            output.write(proposed)
            output.flush()
            os.fsync(output.fileno())
        if staging.read_bytes() != proposed or cfg.read_bytes() != original:
            raise EnableError("La configuración cambió antes de activar el observador.")
        os.replace(staging, cfg)
    finally:
        if staging.exists():
            staging.unlink()
    if cfg.read_bytes() != proposed:
        raise EnableError("La configuración ON no coincide. Usar recuperación OFF.")
    return {
        "enabled": True, "armed": False, "source_backup": str(plan["verified_backup"]),
        "config_backup": str(backup), "config": str(cfg),
        "sha256": plan["updated_sha256"],
        "time": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Activación de PRUEBA VSH sin ARM")
    parser.add_argument("--run", action="store_true",
                        help="Solo con confirmación y backup verificado, OFF->ON")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise EnableError("PPSSPP y PSP Dock real deben estar cerrados.")
        devices = windows_devices()
        if len(devices) != 1:
            raise EnableError("Se esperaba exactamente una PSP Sony conectada por USB.")
        root = Path(devices[0]["root"])
        initial, _ = identify(str(root))
        plan = preflight(root, DEFAULT_DEST, initial.volume_serial,
                         initial.usb_device_id)
        print("PSP:", initial.model, initial.drive)
        print("Backup crítico verificado:", plan["verified_backup"])
        print("Solo cambia PLUGINS.TXT de OFF a ON, SIN ARM.TXT.")
        print("Solo escribe PLUGINS.TXT (en FAT32). No cambia USB, ROM, flash ni partidas.")
        print("Recuperación: START al encender, ARK Plugin Manager -> Nada.")
        print("Alternativa: lector Memory Stick o Recuperar-plugin-VSH.cmd.")
        if not args.run:
            print("VISTA PREVIA: SIN escritura.")
            return 0
        if input("Escribe ACTIVAR OBSERVADOR para preparar ON: ").strip() != "ACTIVAR OBSERVADOR":
            print("Cancelado, el plugin sigue OFF.")
            return 0
        if identify(str(root))[0] != initial:
            raise EnableError("La identidad de la PSP cambió.")
        print(json.dumps(enable_observer_off_to_on(
            root, DEFAULT_DEST, CONFIG_BACKUPS,
            initial.volume_serial, initial.usb_device_id), ensure_ascii=False))
        print("Para probarlo, expulsar Windows normalmente, retirar cable y reiniciar.")
        return 0
    except (EnableError, BackupError, ValueError, OSError, UsbTransitionError) as exc:
        print("ACTIVACIÓN VSH CANCELADA:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
