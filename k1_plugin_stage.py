"""Stage kernel observer K1 on a VERIFIED PSP, always DISABLED.

This tool never enables a new kernel plugin, starts/stops USB drivers,
alters firmware, modifies game data, or ejects a mounted PSP volume.
The staged PRX is separate from the existing tested VSH USER observer.

Before editing PLUGINS.TXT it verifies a PSP->PC critical backup of this
same Sony USB identity and exact current config, and creates a standalone
PC config backup. It refuses to overwrite an existing K1 folder or entry.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import uuid

from dock import ROOT, inside, windows_devices
from install_safe_probe import digest
from lab_eject import ppsspp_open, real_dock_open
from psp_backup import DEFAULT_DEST, BackupError
from psp_usb import identify, UsbTransitionError
from vsh_plugin_enable import validated_backup
from vsh_plugin_stage import CONFIG, ENTRY as VSH_OFF
from vsh_plugin_status import inspect_volume


class K1StageError(ValueError):
    pass


K1_STAGE_DIR = "SEPLUGINS/PSPDockKernelK1"
K1_FILE = "pspdockk1observer.prx"
K1_ENTRY = (
    b"vsh, ms0:/SEPLUGINS/PSPDockKernelK1/pspdockk1observer.prx, off\n"
)
K1_BIN = ROOT / "homebrew/kernel-usb-observer-k1/dist/pspdockk1observer.prx"
K1_SHA256 = "2c9e27e81df4deca71fb071c907272e537a98b9cf4e7a75df3cc0f34891e2092"
CONFIG_BACKUP_DIR = DEFAULT_DEST / "recovery-configs"
_VSH_LINE = VSH_OFF.rstrip(b"\n").replace(b", off", b", on").lower()


def config_with_disabled_k1(original: bytes) -> bytes:
    """Preserve existing lines exactly; append one strictly disabled K1."""
    if not original or len(original) > 128 * 1024 or b"\0" in original:
        raise K1StageError("Configuración PLUGINS.TXT ausente o inválida.")
    lines = original.replace(b"\r", b"").lower().splitlines()
    if lines.count(_VSH_LINE) != 1:
        raise K1StageError("El observador VSH ON existente no es único o ha cambiado.")
    if any(b"pspdockkernelk1" in line or b"pspdockk1observer" in line for line in lines):
        raise K1StageError("K1 ya figura en PLUGINS.TXT; no sobreescribir.")
    if b"\r\n" in original:
        ending = b"\r\n"
    else:
        ending = b"\n"
    separator = b"" if original.endswith(b"\n") else ending
    return original + separator + K1_ENTRY.replace(b"\n", ending)


def preflight_k1(root: Path, backups: Path, serial: str,
                 usb_parent: str, source: Path = K1_BIN) -> dict:
    """Pure reads on PSP. Requires a verified PC backup from this exact PSP."""
    if not source.is_file() or digest(source) != K1_SHA256:
        raise K1StageError("K1 no coincide con el binario PSPDEV revisado.")
    target = inside(root, K1_STAGE_DIR)
    if target.exists():
        raise K1StageError("Carpeta K1 existente: no sobrescribir ni actualizar.")
    config = inside(root, CONFIG)
    if not config.is_file():
        raise K1StageError("Falta PLUGINS.TXT en PSP.")
    before = config.read_bytes()
    after = config_with_disabled_k1(before)
    vsh = inspect_volume(root)
    if (
        vsh["plugin_config"] != "ON"
        or inside(root, "SEPLUGINS/PSPDockProbe/ARM.TXT").exists()
        or not vsh["vsh_prx_present"] or not vsh["helper_prx_present"]
        or not all(vsh["known_prx_integrity"].values())
    ):
        raise K1StageError("VSH conocido no está ON, desarmado (sin ARM) o íntegro.")
    snapshot = validated_backup(backups, serial, usb_parent, before)
    if shutil.disk_usage(root).free < 8 * 1024 * 1024 + source.stat().st_size:
        raise K1StageError("Espacio PSP insuficiente para staging seguro.")
    return {
        "config": config, "before": before, "after": after,
        "backup": snapshot, "target": target, "source": source,
        "old_sha256": hashlib.sha256(before).hexdigest(),
        "new_sha256": hashlib.sha256(after).hexdigest(),
    }


def stage_disabled_k1(root: Path, backups: Path, config_backups: Path,
                      serial: str, usb_parent: str,
                      source: Path = K1_BIN) -> dict:
    """Create ONLY an inert K1 folder and a K1 'off' plugin line."""
    plan = preflight_k1(root, backups, serial, usb_parent, source)
    cfg = plan["config"]
    config_backups.mkdir(parents=True, exist_ok=True)
    saved = config_backups / (
        "PLUGINS.before-k1-off-" + plan["old_sha256"][:20] + ".bak"
    )
    if saved.exists():
        if saved.read_bytes() != plan["before"]:
            raise K1StageError("Backup PC de PLUGINS.TXT distinto; cancelar.")
    else:
        with saved.open("xb") as file:
            file.write(plan["before"])
            file.flush()
            os.fsync(file.fileno())
    if cfg.read_bytes() != plan["before"]:
        raise K1StageError("PLUGINS.TXT cambió después del backup.")

    tmp = inside(root, "SEPLUGINS/.pspdock-k1-" + uuid.uuid4().hex)
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        binary = tmp / K1_FILE
        with source.open("rb") as reader, binary.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if digest(binary) != K1_SHA256:
            raise K1StageError("El PRX staged en PSP difiere del original.")
        if plan["target"].exists() or cfg.read_bytes() != plan["before"]:
            raise K1StageError("PSP cambió durante staging; no activar K1.")
        os.rename(tmp, plan["target"])
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)

    # If this config update fails, the staged folder remains inert and
    # inspectable: no attempt to delete potentially disconnected PSP data.
    if cfg.read_bytes() != plan["before"]:
        raise K1StageError("PLUGINS.TXT cambió antes de registrar OFF.")
    temp_config = cfg.parent / (".k1-disabled-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp_config.open("xb") as writer:
            writer.write(plan["after"])
            writer.flush()
            os.fsync(writer.fileno())
        if temp_config.read_bytes() != plan["after"] or cfg.read_bytes() != plan["before"]:
            raise K1StageError("Cambio en configuración antes de publicar K1 OFF.")
        os.replace(temp_config, cfg)
    finally:
        if temp_config.exists():
            temp_config.unlink()

    if cfg.read_bytes() != plan["after"] or digest(plan["target"] / K1_FILE) != K1_SHA256:
        raise K1StageError("El staging final no coincide; NO activar K1.")
    return {
        "enabled": False, "kernel": K1_FILE, "sha256": K1_SHA256,
        "config_sha256": plan["new_sha256"], "config": str(cfg),
        "source_backup": str(plan["backup"]), "config_backup": str(saved),
        "target": str(plan["target"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PSP Dock K1: stage kernel OFF")
    parser.add_argument("--stage-disabled", action="store_true",
                        help="ÚNICO cambio permitido: copiar PRX y añadir K1 OFF")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise K1StageError("PPSSPP o PSP Dock REAL abiertos.")
        devices = windows_devices()
        if len(devices) != 1:
            raise K1StageError("Se requiere exactamente una PSP Sony por USB nativo.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        plan = preflight_k1(root, DEFAULT_DEST, identity.volume_serial,
                            identity.usb_device_id)
        print("Sony PSP:", identity.drive, identity.model)
        print("Backup fuente:", plan["backup"])
        print("K1 SHA256:", K1_SHA256)
        print("Nuevo PLUGINS.TXT:")
        print(plan["after"].decode("ascii", "strict"))
        print("K1 queda OFF. No arranca en VSH, no controla USB ni FAT.")
        if not args.stage_disabled:
            print("SOLO PREVIEW; no se modifica PSP.")
            return 0
        if input("Escribe K1 OFF para preparar el módulo DESACTIVADO: ").strip() != "K1 OFF":
            print("Cancelado sin cambios.")
            return 0
        if identify(str(root))[0] != identity:
            raise K1StageError("PSP cambió de identidad antes del staging.")
        print("STAGING OK:", stage_disabled_k1(
            root, DEFAULT_DEST, CONFIG_BACKUP_DIR, identity.volume_serial,
            identity.usb_device_id))
        return 0
    except (K1StageError, BackupError, ValueError, OSError, UsbTransitionError) as exc:
        print("K1 NO INSTALADO/NO HABILITADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
