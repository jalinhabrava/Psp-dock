"""PSP Dock: correct physical PSP buttons in private PPSSPP DirectInput profile.

Only modifies PSP Dock's isolated PC-only controls.ini.  Never changes PSP
firmware, HID PRX, global PPSSPP settings, game saves, or storage USB roles.

FuSa/PSPDockHID exposes button bits in this exact order:
  1 Cross, 2 Circle, 3 Square, 4 Triangle, 5 L, 6 R, 7 Select, 8 Start.
PPSSPP's Windows DirectInput PAD_0 uses NKCODE_BUTTON_1 = 188, etc.
The stock controls.ini assumes a different gamepad layout, so physical
Square became virtual Circle in Dariusburst.  Change only the eight DInput
tokens (10-...), leaving keyboard/XInput, analog and POV intact.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import uuid

from lab_eject import ppsspp_open
from ppsspp_hid_session import LAB_DATA, plan_game, verified_prepared_runtime


class MappingError(ValueError):
    pass


# PPSSPP NKCODE_BUTTON_1=188, BUTTON_2=189, ...; DInput device ID 10.
PHYSICAL_BUTTON_TO_KEY = {
    "Cross": 188,     # Button 1
    "Circle": 189,    # Button 2
    "Square": 190,    # Button 3
    "Triangle": 191,  # Button 4
    "L": 192,         # Button 5
    "R": 193,         # Button 6
    "Select": 194,    # Button 7
    "Start": 195,     # Button 8
}

# Exact stock DInput mapping observed in PPSSPP 1.20.4's private profile.
STOCK_DINPUT = {
    "Cross": 189,
    "Circle": 190,
    "Square": 191,
    "Triangle": 188,
    "L": 194,
    "R": 195,
    "Select": 196,
    "Start": 197,
}

_ENTRY_RE = re.compile(r"^(?P<left>[ \t]*)(?P<key>[^=\r\n]+?)(?P<eq>[ \t]*=[ \t]*)(?P<value>[^\r\n]*)(?P<newline>\r\n|\n|\r)?$")
_DEVICE_RE = re.compile(r"^10-\d+$")
_KEY_TOKEN = re.compile(r"^(?:\d+)-\d+$")


def remap_controls(original: bytes) -> tuple[bytes, bool]:
    """Return modified bytes and changed flag; only an exact stock profile.

    All mappings must be STOCK or all already PSP-correct.  Mixed, custom or
    duplicate key entries fail closed, rather than overwriting user choices.
    """
    if not original or len(original) > 128 * 1024 or b"\0" in original:
        raise MappingError("controls.ini ausente o inválido.")
    try:
        text = original.decode("utf-8-sig", "strict")
    except UnicodeDecodeError as exc:
        raise MappingError("controls.ini no está en UTF-8.") from exc
    bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    lines = text.splitlines(keepends=True)
    section = None
    section_count = 0
    found: dict[str, tuple[int, int, list[str], re.Match[str]]] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            if section == "[ControlMapping]":
                section_count += 1
            continue
        if section != "[ControlMapping]" or not stripped or stripped.startswith(("#", ";")):
            continue
        match = _ENTRY_RE.fullmatch(line)
        if not match:
            raise MappingError("Entrada de controls.ini desconocida.")
        key = match.group("key").strip()
        if key not in PHYSICAL_BUTTON_TO_KEY:
            continue
        if key in found:
            raise MappingError(f"Entrada duplicada de control: {key}.")
        parts = [part.strip() for part in match.group("value").split(",")]
        if not parts or any(not _KEY_TOKEN.fullmatch(p) for p in parts):
            raise MappingError(f"Formato de mapeo desconocido para {key}.")
        dinput = [p for p in parts if _DEVICE_RE.fullmatch(p)]
        if len(dinput) != 1:
            raise MappingError(f"Mapeo DirectInput ambiguo para {key}.")
        found[key] = (index, int(dinput[0][3:]), parts, match)
    if section_count != 1 or set(found) != set(PHYSICAL_BUTTON_TO_KEY):
        raise MappingError("Faltan controles PSP o hay secciones duplicadas.")
    actual = {key: entry[1] for key, entry in found.items()}
    if actual == PHYSICAL_BUTTON_TO_KEY:
        return original, False
    if actual != STOCK_DINPUT:
        raise MappingError(
            "El perfil PPSSPP ya tiene una asignación personalizada o parcial. "
            "No la sobrescribo automáticamente."
        )
    for key, (index, _, parts, match) in found.items():
        # Preserve the non-DInput parts and their original ordering.
        replaced = [
            f"10-{PHYSICAL_BUTTON_TO_KEY[key]}" if _DEVICE_RE.fullmatch(p) else p
            for p in parts
        ]
        lines[index] = (
            match.group("left") + match.group("key") + match.group("eq")
            + ",".join(replaced) + (match.group("newline") or "")
        )
    changed = bom + "".join(lines).encode("utf-8")
    if changed == original:
        raise MappingError("No se aplicó ninguna corrección a controls.ini.")
    # Safety: the PSP-correct file must be recognized as fully corrected.
    return changed, True


def profile_path(private_data: Path = LAB_DATA) -> Path:
    """Resolve only PSP Dock's private PC profile, not the real PSP drive."""
    plan = plan_game(private_data=private_data)
    verified_prepared_runtime(plan)
    return plan.private_memstick / "PSP/SYSTEM/controls.ini"


def apply_profile(path: Path, backup_dir: Path, *, approve: bool = False) -> dict:
    """Back up existing controls.ini on PC before an atomic local edit."""
    if ppsspp_open():
        raise MappingError("Cierra PPSSPP antes de modificar sus controles.")
    if not path.is_file() or path.is_symlink() or backup_dir.is_symlink():
        raise MappingError("La configuración privada PPSSPP no existe o no es segura.")
    before = path.read_bytes()
    after, changed = remap_controls(before)
    old_hash = hashlib.sha256(before).hexdigest()
    new_hash = hashlib.sha256(after).hexdigest()
    if not changed:
        return {"changed": False, "status": "PSP-CORRECT", "sha256": old_hash}
    if not approve:
        return {"changed": False, "status": "PREVIEW", "sha256_before": old_hash,
                "sha256_after": new_hash}
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("controls.before-psp-map-" + old_hash[:20] + ".ini")
    if backup.exists():
        if backup.read_bytes() != before:
            raise MappingError("Backup de controls.ini con hash contradictorio.")
    else:
        with backup.open("xb") as fd:
            fd.write(before)
            fd.flush()
            os.fsync(fd.fileno())
    if path.read_bytes() != before:
        raise MappingError("controls.ini cambió antes de publicar la corrección.")
    tmp = path.with_name(".pspdock-controls-" + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("xb") as fd:
            fd.write(after)
            fd.flush()
            os.fsync(fd.fileno())
        if tmp.read_bytes() != after or path.read_bytes() != before:
            raise MappingError("Falló la verificación previa a publicar controls.ini.")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if path.read_bytes() != after:
        raise MappingError("La configuración PPSSPP no coincide con el mapeo esperado.")
    return {"changed": True, "status": "PSP-CORRECT", "backup": str(backup),
            "sha256_before": old_hash, "sha256_after": new_hash}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Corregir botones de PSP real en PPSSPP privado (sin tocar USB)"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Modificar solo controls.ini privado; guardar backup")
    args = parser.parse_args()
    try:
        config = profile_path()
        print("PPSSPP privado:", config)
        result = apply_profile(config, LAB_DATA / "profile-backups", approve=args.apply)
        print(result)
        return 0
    except (MappingError, ValueError, OSError) as exc:
        print("Mapeo no modificado:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
