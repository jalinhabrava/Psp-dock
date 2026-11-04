"""Restore a PSP Dock VSH plugin entry to OFF without touching other plugins.

Intentionally cannot enable plugins or modify ARM markers. By default it
only shows a preview; --run additionally requires a typed confirmation.
Only the expected Sony USB device/Memory Stick is accepted.

This is useful if a future VSH startup test fails: hold START during
boot, open the PSP's native USB mode, then disable our plugin from Windows.
When the PSP cannot access USB, the same PLUGINS.TXT can be edited through
an external Memory Stick reader (this tool will NOT write to an unverified
USB drive).
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import uuid

from dock import ROOT, inside, windows_devices
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from psp_dock.devices.psp_usb import identify, UsbTransitionError
from tools.psp.vsh_plugin_stage import CONFIG, ENTRY


BACKUPS = Path.home() / "Documents" / "PSP-Dock-Backups" / "recovery-configs"
_ENABLED = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on"
_DISABLED = ENTRY.rstrip(b"\n")
_ENABLED_LOWER = _ENABLED.lower()
_DISABLED_LOWER = _DISABLED.lower()


def plan_off(config: bytes) -> tuple[bytes, bool]:
    """Return original bytes if OFF, else change ONLY the one known ON line."""
    if len(config) > 128 * 1024 or b"\0" in config:
        raise ValueError("Configuración ARK no reconocida.")
    original_lines = config.splitlines(keepends=True)
    matched = []
    for index, line in enumerate(original_lines):
        content = line.rstrip(b"\r\n").strip().lower()
        if b"pspdockvsh.prx" in content or b"pspdockprobe" in content:
            matched.append((index, content))
    if len(matched) != 1:
        raise ValueError("No existe UNA entrada PSP Dock única que podamos recuperar.")
    index, line = matched[0]
    if line == _DISABLED_LOWER:
        return config, False
    if line != _ENABLED_LOWER:
        raise ValueError("El plugin está en un estado desconocido: no se sobrescribe.")
    value = original_lines[index]
    newline = b"\r\n" if value.endswith(b"\r\n") else b"\n" if value.endswith(b"\n") else b""
    original_lines[index] = _DISABLED + newline
    return b"".join(original_lines), True


def restore_off(root: Path, backup_dir: Path) -> dict:
    config = inside(root, CONFIG)
    if not config.is_file():
        raise ValueError("PLUGINS.TXT no existe, no se puede restaurar.")
    before = config.read_bytes()
    after, changed = plan_off(before)
    if not changed:
        return {"changed": False, "config": str(config), "backup": None}
    backup_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(before).hexdigest()
    original_copy = backup_dir / ("PLUGINS.before-recovery-" + sha + ".bak")
    if not original_copy.exists():
        with original_copy.open("xb") as output:
            output.write(before)
            output.flush()
            os.fsync(output.fileno())
    elif original_copy.read_bytes() != before:
        raise ValueError("Backup de configuración en PC no coincide.")
    if config.read_bytes() != before:
        raise ValueError("PLUGINS.TXT cambió durante la preparación.")
    temp = config.parent / (".plugins-off-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as output:
            output.write(after)
            output.flush()
            os.fsync(output.fileno())
        if temp.read_bytes() != after or config.read_bytes() != before:
            raise ValueError("Cambios inesperados antes de restaurar OFF.")
        os.replace(temp, config)
    finally:
        if temp.exists():
            temp.unlink()
    if config.read_bytes() != after:
        raise ValueError("OFF no verificado: comprobar configuración manualmente.")
    return {"changed": True, "config": str(config),
            "backup": str(original_copy)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recuperación PSP Dock VSH -> OFF")
    parser.add_argument("--run", action="store_true",
                        help="Confirmar cambio de la entrada PSP Dock a OFF")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise ValueError("Cierra PPSSPP y PSP Dock real antes de recuperar.")
        devices = windows_devices()
        if len(devices) != 1:
            raise ValueError("Se requiere PSP Sony verificada y conectada por USB nativo.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        config = inside(root, CONFIG)
        if not config.is_file():
            raise ValueError("La consola no contiene PLUGINS.TXT.")
        _, changed = plan_off(config.read_bytes())
        print("PSP:", identity.model, identity.drive)
        print("Configuración:", config)
        print("PSP Dock ahora:", "ON; se puede cambiar solo a OFF" if changed
              else "OFF; no hay nada que hacer")
        if not changed or not args.run:
            print("No se ha realizado ninguna escritura.")
            return 0
        if input("Escribe DESACTIVAR PSP DOCK para confirmar: ").strip() != "DESACTIVAR PSP DOCK":
            print("Cancelado sin cambios.")
            return 0
        if identify(str(root))[0] != identity:
            raise ValueError("La identidad Sony PSP cambió.")
        print("PLUGIN DESACTIVADO:", restore_off(root, BACKUPS))
        print("Expulsa desde Windows normalmente antes de desconectar.")
        return 0
    except (OSError, UsbTransitionError, ValueError) as exc:
        print("RECUPERACIÓN NO REALIZADA:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
