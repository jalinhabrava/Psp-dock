"""Opt-in K1 export proof marker management on a verified Sony PSP.

Default: read-only status. --arm is a later-stage test ONLY AFTER a
successful physical boot with K1 Proof ON and *without* ARM. It creates
an exact ARM.TXT, without changing PLUGINS.TXT or any installed PRX.

--disarm removes only the exact known ARM token and preserves the
K1PROOF.TXT evidence file. Recovery does not require a healthy kernel
PRX. A proof file is an offline diagnostic, NEVER a live USB/FAT ACK.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import re

from dock import inside, windows_devices
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from tools.psp.psp_backup import DEFAULT_DEST, BackupError
from psp_dock.devices.psp_usb import identify, UsbTransitionError
from tools.psp.k1_plugin_stage import K1StageError, CONFIG
from tools.psp.k1_proof_admin import (
    PROOF_ARM_TOKEN, PROOF_STAGE, PROOF_ON, PROOF_SHA256,
    PROOF_NAME, _matches, _proof_lines, _validate_existing_k1, inspect_proof,
)
from tools.psp.vsh_plugin_enable import validated_backup
from tools.psp.vsh_plugin_stage import FILES, STAGE_DIR

_HEX8 = re.compile(r"^[0-9A-F]{8}$")
_FIELDS = ("MAGIC", "VERSION", "CAPABILITIES", "STATE",
           "USB", "BUS", "STORAGE")


def parse_proof_marker(payload: bytes) -> dict:
    try:
        lines = payload.decode("ascii", "strict").splitlines()
    except UnicodeError as exc:
        raise K1StageError("K1PROOF.TXT contiene bytes no ASCII.") from exc
    if len(lines) != 9 or lines[0] != "PSPDOCK_K1_PROOF=01":
        raise K1StageError("Cabecera o longitud de prueba K1 desconocida.")
    if lines[-1] != "ROLE_SWITCHED=0":
        raise K1StageError("K1 Proof no garantiza ausencia de cambio de rol.")
    values = {}
    for expected, line in zip(_FIELDS, lines[1:8]):
        prefix = expected + "="
        if not line.startswith(prefix) or not _HEX8.fullmatch(line[len(prefix):]):
            raise K1StageError("Campo hexadecimal K1 Proof incorrecto: " + expected)
        values[expected.lower()] = int(line[len(prefix):], 16)
    if (values["magic"], values["version"], values["capabilities"],
            values["state"]) != (0x50444B31, 1, 0, 1):
        raise K1StageError("K1 no demuestra versión 1, READY y capacidades cero.")
    if values["usb"] & (0x200 | 0x020 | 0x002):
        raise K1StageError("USB en K1 Proof estaba conectado o activo.")
    values["role_switch_confirmed"] = False
    values["exclusive_fat_confirmed"] = False
    return values


def plan_arm(root: Path, backup_root: Path,
             serial: str, usb_parent: str) -> dict:
    """Full read-only preflight; the physical first-boot check is external."""
    # plan_enable validates everything except the Proof ON line; use its
    # underlying archive/PRX gates through the installed current config.
    config = inside(root, CONFIG)
    before = config.read_bytes()
    if _matches(before, PROOF_ON) != 1 or len(_proof_lines(before)) != 1:
        raise K1StageError("K1 Proof no está ON exactamente una vez.")
    _validate_existing_k1(root, before)
    status = inspect_proof(root)
    if status["proof_config"] != "ON" or not status["prx_integrity"]:
        raise K1StageError("K1 Proof ON sin integridad comprobada.")
    folder = inside(root, PROOF_STAGE)
    if (folder / "ARM.TXT").exists() or (folder / "K1PROOF.TXT").exists():
        raise K1StageError("ARM o K1PROOF.TXT previos: no repetir sin analizar.")
    vsh_dir = inside(root, STAGE_DIR)
    for filename, (_, checksum) in FILES.items():
        from tools.psp.install_safe_probe import digest
        if digest(vsh_dir / filename) != checksum:
            raise K1StageError("Observador VSH anterior alterado.")
    if (vsh_dir / "ARM.TXT").exists():
        raise K1StageError("Observador VSH original todavía armado.")
    # Require EXACT backup of this new ON config from same PSP.
    backup = validated_backup(backup_root, serial, usb_parent, before)
    return {"folder": folder, "config": config, "config_bytes": before,
            "backup": backup}


def arm(root: Path, backup_root: Path,
        serial: str, usb_parent: str) -> dict:
    plan = plan_arm(root, backup_root, serial, usb_parent)
    if plan["config"].read_bytes() != plan["config_bytes"]:
        raise K1StageError("PLUGINS.TXT cambió tras analizar.");
    marker = plan["folder"] / "ARM.TXT"
    with marker.open("xb") as output:
        output.write(PROOF_ARM_TOKEN)
        output.flush()
        os.fsync(output.fileno())
    if marker.read_bytes() != PROOF_ARM_TOKEN:
        raise K1StageError("No se pudo verificar ARM de K1 Proof.")
    return {"armed": True, "marker": str(marker),
            "config_unchanged": True, "backup": str(plan["backup"])}


def disarm(root: Path) -> dict:
    folder = inside(root, PROOF_STAGE)
    marker = folder / "ARM.TXT"
    result = folder / "K1PROOF.TXT"
    if not marker.exists():
        return {"changed": False, "armed": False,
                "proof_preserved": result.is_file()}
    if not marker.is_file() or marker.read_bytes() != PROOF_ARM_TOKEN:
        raise K1StageError("ARM desconocido: se conserva para revisión manual.")
    marker.unlink()
    if marker.exists():
        raise K1StageError("ARM no se pudo retirar.")
    return {"changed": True, "armed": False,
            "proof_preserved": result.is_file()}


def read_status(root: Path) -> dict:
    info = inspect_proof(root)
    result = inside(root, PROOF_STAGE + "/K1PROOF.TXT")
    info["proof"] = None
    info["proof_error"] = None
    if result.is_file():
        try:
            info["proof"] = parse_proof_marker(result.read_bytes())
        except (K1StageError, OSError) as exc:
            info["proof_error"] = str(exc)
    info["warning"] = "Archivo diagnóstico, no ACK USB/FAT en tiempo real."
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description="PSP K1 Proof: consulta y ARM reversible")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--arm", action="store_true",
                         help="SOLO tras éxito físico ON sin ARM")
    actions.add_argument("--disarm", action="store_true",
                         help="Retira el ARM exacto, no borra la evidencia")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise K1StageError("Cerrar PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise K1StageError("Conecta una única PSP Sony por USB nativo.")
        root = Path(devices[0]["root"])
        original, _ = identify(str(root))
        print("PSP verificada:", original.drive, original.model)
        if not args.arm and not args.disarm:
            print(read_status(root))
            print("Solo lectura. No se ha modificado nada.")
            return 0
        if args.arm:
            plan = plan_arm(root, DEFAULT_DEST, original.volume_serial,
                            original.usb_device_id)
            print("K1 Proof validado; backup ON exacto:", plan["backup"])
            print("Esto ARMARÁ el próximo arranque; no cambia USB ni HID.")
            confirm = "ARMAR K1 PROOF TRAS BOOT"
        else:
            folder = inside(root, PROOF_STAGE)
            marker = folder / "ARM.TXT"
            if not marker.exists():
                print("ARM ausente. No se modifica nada.")
                return 0
            if not marker.is_file() or marker.read_bytes() != PROOF_ARM_TOKEN:
                raise K1StageError("ARM no coincide con el token seguro.")
            confirm = "DESARMAR K1 PROOF"
        if input("Escribe " + confirm + ": ").strip() != confirm:
            print("Cancelado sin cambios.")
            return 0
        if identify(str(root))[0] != original:
            raise K1StageError("La PSP cambió de identidad.")
        print(arm(root, DEFAULT_DEST, original.volume_serial, original.usb_device_id)
              if args.arm else disarm(root))
        return 0
    except (K1StageError, BackupError, OSError, UsbTransitionError, ValueError) as exc:
        print("K1 PROOF CANCELADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
