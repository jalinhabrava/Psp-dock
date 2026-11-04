"""Controlled, reversible preparation of a K1 export proof module.

This tool only stages a separately reviewed kernel PRX as an ARK plugin,
enables it WITHOUT ARM after a same-device backup, or recovers it ON->OFF.
No role transitions, USB commands, flash, SAVEDATA writes or host eject.
It intentionally NEVER creates an ARM marker: a later explicit test
controls the sole optional K1PROOF.TXT firmware-side write.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from dock import ROOT, inside, windows_devices
from tools.psp.install_safe_probe import digest
from tools.psp.k1_plugin_stage import K1_FILE, K1_SHA256, K1_STAGE_DIR, CONFIG, K1StageError
from tools.psp.k1_plugin_enable import K1_ON_LINE
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from tools.psp.psp_backup import DEFAULT_DEST, BackupError
from psp_dock.devices.psp_usb import identify, UsbTransitionError
from tools.psp.vsh_plugin_enable import validated_backup
from tools.psp.vsh_plugin_stage import ARM_TOKEN, STAGE_DIR
from tools.psp.vsh_plugin_status import inspect_volume

PROOF_STAGE = "SEPLUGINS/PSPDockK1Proof"
PROOF_NAME = "pspdockk1proof.prx"
PROOF_SOURCE = ROOT / "homebrew/k1-proof-vsh/dist/pspdockk1proof.prx"
PROOF_SHA256 = "df215ae101fc37d939172ac68037450e53ab112df7d5731a5a119174b4d0fe75"
PROOF_OFF = b"vsh, ms0:/SEPLUGINS/PSPDockK1Proof/pspdockk1proof.prx, off"
PROOF_ON = PROOF_OFF.replace(b", off", b", on")
PROOF_ARM_TOKEN = b"PSPDOCK-K1-PROOF-ARM-01\n"
BACKUP_DIR = DEFAULT_DEST / "recovery-configs"


def _matches(config: bytes, target: bytes) -> int:
    return config.replace(b"\r", b"").lower().splitlines().count(target.lower())


def _proof_lines(config: bytes) -> list[bytes]:
    return [
        x.strip().lower() for x in config.splitlines()
        if b"pspdockk1proof" in x.lower() or b"pspdockk1proof/" in x.lower()
    ]


def _validate_existing_k1(root: Path, config: bytes) -> None:
    if not config or len(config) > 128 * 1024 or b"\0" in config:
        raise K1StageError("PLUGINS.TXT inválido.")
    vsh_on = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on"
    if _matches(config, vsh_on) != 1 or _matches(config, K1_ON_LINE) != 1:
        raise K1StageError("VSH original y K1 kernel ON no son únicos.")
    vsh = inspect_volume(root)
    if (vsh["plugin_config"] != "ON"
        or not all(vsh["known_prx_integrity"].values())
        or inside(root, STAGE_DIR + "/ARM.TXT").exists()):
        raise K1StageError("El observador original no está íntegro y desarmado.")
    k1 = inside(root, K1_STAGE_DIR + "/" + K1_FILE)
    if not k1.is_file() or digest(k1) != K1_SHA256:
        raise K1StageError("K1 kernel actual no coincide con su binario comprobado.")


def _ensure_proof_source(source: Path = PROOF_SOURCE) -> None:
    if not source.is_file() or digest(source) != PROOF_SHA256:
        raise K1StageError("El nuevo PRX de K1 Proof no coincide con el SHA congelado.")


def _replace_exact(config: bytes, old: bytes, new: bytes) -> bytes:
    if _matches(config, old) != 1 or len(_proof_lines(config)) != 1:
        raise K1StageError("No existe exactamente una entrada Proof conocida.")
    result = []
    for line in config.splitlines(keepends=True):
        if line.strip().lower() == old.lower():
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
            result.append(new + ending)
        else:
            result.append(line)
    updated = b"".join(result)
    if _matches(updated, new) != 1 or _matches(updated, old):
        raise K1StageError("La transición Proof no ha producido la configuración esperada.")
    return updated


def config_for_stage(config: bytes) -> bytes:
    if not config or len(config) > 128 * 1024 or b"\0" in config:
        raise K1StageError("PLUGINS.TXT inválido.")
    vsh_on = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on"
    if _matches(config, vsh_on) != 1 or _matches(config, K1_ON_LINE) != 1:
        raise K1StageError("El VSH original y K1 ON deben estar registrados exactamente una vez.")
    if _proof_lines(config):
        raise K1StageError("Ya existe K1 Proof registrado; no crear duplicados.")
    ending = b"\r\n" if b"\r\n" in config else b"\n"
    sep = b"" if config.endswith(b"\n") else ending
    return config + sep + PROOF_OFF + ending


def plan_stage(root: Path, backup_root: Path, serial: str, usb_parent: str) -> dict:
    _ensure_proof_source()
    cfg = inside(root, CONFIG)
    original = cfg.read_bytes()
    _validate_existing_k1(root, original)
    proposal = config_for_stage(original)
    target = inside(root, PROOF_STAGE)
    if target.exists():
        raise K1StageError("Ya existe la carpeta K1Proof; no sobrescribir.")
    if shutil.disk_usage(root).free < PROOF_SOURCE.stat().st_size + 8 * 1024 * 1024:
        raise K1StageError("Espacio insuficiente en PSP.")
    backup = validated_backup(backup_root, serial, usb_parent, original)
    return {"config": cfg, "before": original, "after": proposal,
            "target": target, "backup": backup}


def plan_enable(root: Path, backup_root: Path, serial: str, usb_parent: str) -> dict:
    cfg = inside(root, CONFIG)
    current = cfg.read_bytes()
    _validate_existing_k1(root, current)
    target = inside(root, PROOF_STAGE)
    _ensure_proof_source()
    binary = target / PROOF_NAME
    if not binary.is_file() or digest(binary) != PROOF_SHA256:
        raise K1StageError("K1 Proof instalado no corresponde al SHA verificado.")
    if (target / "ARM.TXT").exists() or (target / "K1PROOF.TXT").exists():
        raise K1StageError("K1 Proof está armado o tiene un marcador anterior.")
    updated = _replace_exact(current, PROOF_OFF, PROOF_ON)
    backup = validated_backup(backup_root, serial, usb_parent, current)
    return {"config": cfg, "before": current, "after": updated,
            "target": target, "backup": backup}


def plan_recover(config: bytes) -> tuple[bytes, bool]:
    if not config or len(config) > 128 * 1024 or b"\0" in config:
        raise K1StageError("PLUGINS.TXT inválido.")
    if _matches(config, PROOF_OFF) == 1 and len(_proof_lines(config)) == 1:
        return config, False
    return _replace_exact(config, PROOF_ON, PROOF_OFF), True


def _save_original(directory: Path, prefix: str, original: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = prefix + hashlib.sha256(original).hexdigest()[:20] + ".bak"
    path = directory / name
    if path.is_file():
        if path.read_bytes() != original:
            raise K1StageError("Backup PC encontrado con contenido distinto.")
    else:
        with path.open("xb") as output:
            output.write(original)
            output.flush()
            os.fsync(output.fileno())
    return path


def _publish_config(path: Path, before: bytes, after: bytes) -> None:
    if path.read_bytes() != before:
        raise K1StageError("La configuración PSP cambió durante el preflight.")
    staging = path.parent / (".k1proof-config-" + uuid.uuid4().hex + ".tmp")
    try:
        with staging.open("xb") as output:
            output.write(after)
            output.flush()
            os.fsync(output.fileno())
        if staging.read_bytes() != after or path.read_bytes() != before:
            raise K1StageError("Cambio inesperado en configuración PSP.")
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()
    if path.read_bytes() != after:
        raise K1StageError("PLUGINS.TXT tras cambiar no coincide con el plan.")


def stage_off(root: Path, backups: Path, config_backups: Path,
              serial: str, usb_parent: str) -> dict:
    plan = plan_stage(root, backups, serial, usb_parent)
    saved = _save_original(config_backups, "PLUGINS.before-proof-stage-",
                           plan["before"])
    if plan["config"].read_bytes() != plan["before"]:
        raise K1StageError("Configuración cambió antes del staging.")
    temp = inside(root, "SEPLUGINS/.k1proof-stage-" + uuid.uuid4().hex)
    temp.mkdir(parents=True, exist_ok=False)
    try:
        new_file = temp / PROOF_NAME
        with PROOF_SOURCE.open("rb") as inp, new_file.open("xb") as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        if digest(new_file) != PROOF_SHA256:
            raise K1StageError("PRX copiado no supera verificación SHA256.")
        if plan["target"].exists() or plan["config"].read_bytes() != plan["before"]:
            raise K1StageError("Destino o configuración PSP cambiaron.")
        os.rename(temp, plan["target"])
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    # A staged folder without a PLUGINS.TXT entry is inert.
    _publish_config(plan["config"], plan["before"], plan["after"])
    return {"stage": "OFF", "sha256": PROOF_SHA256,
            "config_sha256": hashlib.sha256(plan["after"]).hexdigest(),
            "config_backup": str(saved), "verified_backup": str(plan["backup"])}


def enable_unarmed(root: Path, backups: Path, config_backups: Path,
                   serial: str, usb_parent: str) -> dict:
    plan = plan_enable(root, backups, serial, usb_parent)
    saved = _save_original(config_backups, "PLUGINS.before-proof-on-",
                           plan["before"])
    _publish_config(plan["config"], plan["before"], plan["after"])
    return {"proof": "ON", "armed": False,
            "config_sha256": hashlib.sha256(plan["after"]).hexdigest(),
            "config_backup": str(saved), "verified_backup": str(plan["backup"])}


def recover_off(root: Path, config_backups: Path) -> dict:
    cfg = inside(root, CONFIG)
    original = cfg.read_bytes()
    restored, changed = plan_recover(original)
    if not changed:
        return {"changed": False, "proof": "OFF"}
    saved = _save_original(config_backups, "PLUGINS.before-proof-recovery-",
                           original)
    _publish_config(cfg, original, restored)
    return {"changed": True, "proof": "OFF", "config_backup": str(saved)}


def inspect_proof(root: Path) -> dict:
    cfg = inside(root, CONFIG)
    config = cfg.read_bytes()
    folder = inside(root, PROOF_STAGE)
    binary = folder / PROOF_NAME
    return {
        "proof_config": ("ON" if _matches(config, PROOF_ON) == 1 and len(_proof_lines(config)) == 1
                         else "OFF" if _matches(config, PROOF_OFF) == 1 and len(_proof_lines(config)) == 1
                         else "UNKNOWN"),
        "prx_present": binary.is_file(),
        "prx_integrity": binary.is_file() and digest(binary) == PROOF_SHA256,
        "armed": (folder / "ARM.TXT").is_file(),
        "proof_marker_exists": (folder / "K1PROOF.TXT").is_file(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="K1 Proof: instalación reversible sin ARM")
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--stage-disabled", action="store_true")
    flags.add_argument("--enable-unarmed", action="store_true")
    flags.add_argument("--recover-off", action="store_true")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise K1StageError("Cerrar PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise K1StageError("Se requiere una PSP Sony en USB nativo.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        if args.stage_disabled:
            plan = plan_stage(root, DEFAULT_DEST, identity.volume_serial,
                              identity.usb_device_id)
            operation = "STAGE OFF"
        elif args.enable_unarmed:
            plan = plan_enable(root, DEFAULT_DEST, identity.volume_serial,
                               identity.usb_device_id)
            operation = "ENABLE ON WITHOUT ARM"
        elif args.recover_off:
            plan = {"config": inside(root, CONFIG)}
            restored, changed = plan_recover(plan["config"].read_bytes())
            if not changed:
                print("K1 Proof ya está OFF, no se modifica nada.")
                return 0
            operation = "RECOVER OFF"
        else:
            print("Sony PSP:", identity.drive, identity.model)
            print(json.dumps(inspect_proof(root), ensure_ascii=False, indent=2))
            print("Solo lectura. No se ha escrito nada.")
            return 0
        print("Sony PSP:", identity.drive, identity.model)
        print("Operación:", operation)
        print("Configuración:", plan["config"])
        if "backup" in plan:
            print("Backup PSP verificado:", plan["backup"])
        print("No se tocarán guardados, firmware, controladores USB ni HID.")
        if input("Escribe " + operation + " para confirmar: ").strip() != operation:
            print("Cancelado sin cambios.")
            return 0
        if identify(str(root))[0] != identity:
            raise K1StageError("La identidad USB de la PSP cambió.")
        if args.stage_disabled:
            outcome = stage_off(root, DEFAULT_DEST, BACKUP_DIR,
                                identity.volume_serial, identity.usb_device_id)
        elif args.enable_unarmed:
            outcome = enable_unarmed(root, DEFAULT_DEST, BACKUP_DIR,
                                     identity.volume_serial, identity.usb_device_id)
        else:
            outcome = recover_off(root, BACKUP_DIR)
        print("Resultado:", json.dumps(outcome, ensure_ascii=False))
        return 0
    except (K1StageError, BackupError, OSError, UsbTransitionError, ValueError) as exc:
        print("OPERACIÓN K1 PROOF CANCELADA:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
