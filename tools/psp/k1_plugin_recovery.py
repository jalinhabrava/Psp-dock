"""Turn ONLY the known kernel K1 plugin ON->OFF for safe PSP recovery.

Recovery intentionally requires no working K1 PRX, archive, or observer ARM.
It never disables the already proven USER VSH observer or alters SAVEDATA.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import uuid

from dock import inside, windows_devices
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from psp_dock.devices.psp_usb import identify, UsbTransitionError
from tools.psp.k1_plugin_stage import K1_ENTRY, K1StageError, CONFIG, CONFIG_BACKUP_DIR

K1_OFF_LINE = K1_ENTRY.rstrip(b"\n")
K1_ON_LINE = K1_OFF_LINE.replace(b", off", b", on")
_K1_TAGS = (b"pspdockkernelk1", b"pspdockk1observer")


def plan_off_k1(config_bytes: bytes) -> tuple[bytes, bool]:
    if not config_bytes or len(config_bytes) > 128 * 1024 or b"\0" in config_bytes:
        raise K1StageError("PLUGINS.TXT vacío o inválido.")
    lines = config_bytes.splitlines(keepends=True)
    found = []
    for index, line in enumerate(lines):
        cleaned = line.strip().lower()
        if any(tag in cleaned for tag in _K1_TAGS):
            found.append((index, cleaned))
    if len(found) != 1:
        raise K1StageError("No hay exactamente una entrada K1 para recuperar.")
    index, cleaned = found[0]
    if cleaned == K1_OFF_LINE.lower():
        return config_bytes, False
    if cleaned != K1_ON_LINE.lower():
        raise K1StageError("K1 está en un estado desconocido: no sobrescribir.")
    line = lines[index]
    newline = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
    lines[index] = K1_OFF_LINE + newline
    return b"".join(lines), True


def recover_k1_off(root: Path, backup_dir: Path) -> dict:
    cfg = inside(root, CONFIG)
    before = cfg.read_bytes()
    after, changed = plan_off_k1(before)
    if not changed:
        return {"changed": False, "k1": "OFF"}
    backup_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(before).hexdigest()
    saved = backup_dir / ("PLUGINS.before-k1-recovery-" + sha + ".bak")
    if saved.exists():
        if saved.read_bytes() != before:
            raise K1StageError("Backup PC de K1 ON distinto.")
    else:
        with saved.open("xb") as writer:
            writer.write(before)
            writer.flush()
            os.fsync(writer.fileno())
    if cfg.read_bytes() != before:
        raise K1StageError("PLUGINS.TXT cambió antes de recuperar.")
    tmp = cfg.parent / (".k1-off-" + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("xb") as writer:
            writer.write(after)
            writer.flush()
            os.fsync(writer.fileno())
        if tmp.read_bytes() != after or cfg.read_bytes() != before:
            raise K1StageError("Error de verificación antes de K1 OFF.")
        os.replace(tmp, cfg)
    finally:
        if tmp.exists():
            tmp.unlink()
    if cfg.read_bytes() != after:
        raise K1StageError("K1 OFF no verificado en Memory Stick.")
    return {"changed": True, "k1": "OFF", "backup": str(saved),
            "config": str(cfg)}


def main() -> int:
    parser = argparse.ArgumentParser(description="PSP K1: recuperación ON a OFF")
    parser.add_argument("--run", action="store_true", help="Requiere confirmación escrita")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise K1StageError("Cerrar PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise K1StageError("Se necesita una PSP Sony en USB nativo.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        cfg = inside(root, CONFIG)
        _, changed = plan_off_k1(cfg.read_bytes())
        print("PSP:", identity.drive, identity.model)
        print("K1 actual:", "ON" if changed else "OFF")
        if not changed or not args.run:
            print("Solo vista previa; no se escribe nada.")
            return 0
        if input("Escribe K1 RECUPERAR OFF: ").strip() != "K1 RECUPERAR OFF":
            print("Cancelado.")
            return 0
        if identify(str(root))[0] != identity:
            raise K1StageError("PSP Sony distinta antes de recuperación.")
        print(recover_k1_off(root, CONFIG_BACKUP_DIR))
        return 0
    except (K1StageError, ValueError, OSError, UsbTransitionError) as exc:
        print("RECUPERACIÓN K1 NO REALIZADA:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
