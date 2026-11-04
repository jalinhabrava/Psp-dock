"""PSP Dock VSH observer installer — PREVIEW ONLY by default.

The program can STAGE two checked binaries and add a *disabled* VSH line to
ARK-5's standard ms0:/SEPLUGINS/PLUGINS.TXT. It NEVER enables or runs a
plugin, starts USB, modifies flash/UMD, or writes to SAVEDATA.

An enabled-on-boot plugin is a separate high-risk operation not authorized
by staging. No arm marker is ever created here. No automatic rollback is
attempted on a volume that may have disappeared during a write; a PC backup
is preserved before any existing plugin configuration is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import uuid

from dock import ROOT, inside, windows_devices
from tools.psp.install_safe_probe import digest
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from psp_dock.devices.psp_usb import UsbTransitionError, identify

CONFIG = "SEPLUGINS/PLUGINS.TXT"
STAGE_DIR = "SEPLUGINS/PSPDockProbe"
ENTRY = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, off\n"
ARM_TOKEN = b"PSPDOCK-OBSERVER-ARM-01\n"

FILES = {
    "pspdockvsh.prx": (
        "homebrew/vsh-observer/dist/pspdockvsh.prx",
        "ea5974c6d3a14f70069265a5c865c5a1b55e2df5737ea864776440dd3bebaf63",
    ),
    "usbstate.prx": (
        "homebrew/usb-driver-status-v07/dist/usbstate.prx",
        "dcb2a33e7418a8f4d48d93485c9d53db2173e0bf059a857caf8f440d80dff457",
    ),
}


def plan_config(existing: bytes) -> bytes:
    """Preserve arbitrary existing config bytes, add EXACTLY one OFF entry."""
    if len(existing) > 128 * 1024 or b"\x00" in existing:
        raise ValueError("PLUGINS.TXT no parece un archivo de texto seguro.")
    if b"pspdockvsh.prx" in existing.lower() or b"pspdockprobe" in existing.lower():
        raise ValueError("Ya hay una entrada PSP Dock; revisión manual requerida.")
    if not existing:
        return ENTRY
    separator = b"" if existing.endswith(b"\n") else b"\r\n" if b"\r\n" in existing else b"\n"
    newline = b"\r\n" if b"\r\n" in existing else b"\n"
    return existing + separator + ENTRY.replace(b"\n", newline)


def _sources() -> dict[str, tuple[Path, str]]:
    return {name: (ROOT / rel, checksum) for name, (rel, checksum) in FILES.items()}


def validate_source_files(sources: dict[str, tuple[Path, str]]) -> None:
    if set(sources) != set(FILES):
        raise ValueError("El paquete VSH debe contener exactamente los dos PRX autorizados.")
    for name, (source, checksum) in sources.items():
        if Path(name).name != name or not source.is_file() or digest(source) != checksum:
            raise ValueError("Binario PSP VSH ausente o no verificado: " + name)


def inspect_target(psp_root: Path, sources: dict[str, tuple[Path, str]]) -> dict:
    """Read-only inspection; does not trust a caller's root identity by itself."""
    validate_source_files(sources)
    if not (psp_root / "PSP/GAME").is_dir():
        raise ValueError("La raíz no parece una Memory Stick PSP montada.")
    target = inside(psp_root, STAGE_DIR)
    if target.exists():
        raise ValueError("Ya existe la carpeta PSPDockProbe. No se sobreescribe.")
    config = inside(psp_root, CONFIG)
    existing = config.read_bytes() if config.exists() else b""
    next_config = plan_config(existing)
    required = sum(src.stat().st_size for src, _ in sources.values()) + len(next_config)
    if shutil.disk_usage(psp_root).free < required + 8 * 1024 * 1024:
        raise ValueError("No hay espacio suficiente para instalar el plugin de forma segura.")
    return {
        "target": target, "config": config,
        "before": existing, "after": next_config,
        "config_sha256": hashlib.sha256(existing).hexdigest(),
        "new_config_sha256": hashlib.sha256(next_config).hexdigest(),
    }


def stage_disabled_plugin(psp_root: Path, sources: dict[str, tuple[Path, str]],
                          backup_dir: Path) -> dict:
    """Stage a new folder, preserve PC backup, atomically publish OFF config.

    Never enables the plugin. Does not remove an incomplete stage if FAT
    might be having issues: any remaining stage is inert and inspectable.
    """
    preview = inspect_target(psp_root, sources)
    target = preview["target"]
    config = preview["config"]
    backup_dir.mkdir(parents=True, exist_ok=True)
    if config.exists():
        backup = backup_dir / (
            "PLUGINS.TXT.before-pspdock-" + preview["config_sha256"][:16] + ".bak"
        )
        if backup.exists():
            if backup.read_bytes() != preview["before"]:
                raise ValueError("Existe una copia de seguridad distinta con el mismo nombre.")
        else:
            with backup.open("xb") as output:
                output.write(preview["before"])
                output.flush()
                os.fsync(output.fileno())
    else:
        backup = None

    tmpdir = inside(psp_root, "SEPLUGINS/.pspdock-stage-" + uuid.uuid4().hex)
    tmpdir.mkdir(parents=True, exist_ok=False)
    try:
        for filename, (source, checksum) in sources.items():
            output = tmpdir / filename
            with source.open("rb") as inp, output.open("xb") as out:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            if digest(output) != checksum:
                raise ValueError("La copia del plugin no coincide: " + filename)
        if target.exists():
            raise ValueError("Otra instalación creó la carpeta antes de esta operación.")
        os.rename(tmpdir, target)
    finally:
        if tmpdir.exists():
            # Only cleaning our empty/incomplete *new* stage directory.
            # If the volume disconnected, any error is allowed to propagate.
            shutil.rmtree(tmpdir)

    if config.exists() and config.read_bytes() != preview["before"]:
        raise ValueError("PLUGINS.TXT ha cambiado durante la copia; NO se modifica.")
    config.parent.mkdir(parents=True, exist_ok=True)
    temp = config.parent / (".plugins-pspdock-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as output:
            output.write(preview["after"])
            output.flush()
            os.fsync(output.fileno())
        if temp.read_bytes() != preview["after"]:
            raise ValueError("El archivo PLUGINS.TXT temporal no coincide.")
        if config.exists() and config.read_bytes() != preview["before"]:
            raise ValueError("La configuración cambió antes de publicarse.")
        os.replace(temp, config)
    finally:
        if temp.exists():
            temp.unlink()

    if config.read_bytes() != preview["after"]:
        raise ValueError("PLUGINS.TXT final no coincide. Restaura desde backup.")
    return {"folder": str(target), "config": str(config),
            "backup": str(backup) if backup else None,
            "enabled": False, "armed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="PSP Dock VSH: previsualizar o preparar OFF")
    parser.add_argument("--stage-disabled", action="store_true",
                        help="Solo con confirmación: copia PRX y añade entrada vsh OFF")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise ValueError("Cierra PPSSPP y el servicio PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise ValueError("Conecta una sola PSP Sony por USB nativo del XMB.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        sources = _sources()
        plan = inspect_target(root, sources)
        print("PSP detectada:", identity.model, identity.drive)
        print("Binarios nuevos, solo en:", plan["target"])
        print("Configuración del sistema existente:", plan["config"])
        print("Se añade literalmente:", ENTRY.decode().strip())
        print("PSP Dock NO se activará en el arranque y NO estará armado.")
        print("El plugin no activa USB, ni HID, ni almacenamiento.")
        if not args.stage_disabled:
            print("PREVISUALIZACIÓN COMPLETA. No se ha escrito nada.")
            return 0
        if input("Escribe STAGE DISABLED para copiar (otra cosa cancela): ").strip() != "STAGE DISABLED":
            print("Cancelado sin escritura.")
            return 0
        if identify(str(root))[0] != identity:
            raise ValueError("La consola USB cambió mientras se confirmaba.")
        result = stage_disabled_plugin(root, sources, ROOT / "data/vsh-config-backups")
        print("COPIA VERIFICADA, plugin DESACTIVADO:", result)
        print("Expulsa la PSP normalmente en Windows; no actives el plugin todavía.")
        return 0
    except (ValueError, UsbTransitionError, OSError) as exc:
        print("VSH STAGING CANCELADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
