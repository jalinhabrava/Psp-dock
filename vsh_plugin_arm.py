"""Optional explicitly authorized arming of the staged VSH observer.

Arming does not enable or start the ARK plugin. It only creates an opt-in
sentinel file under the NEW PSP Dock plugin folder, after verifying the
disabled ARK config and the checksummed binaries. It never touches SAVEDATA.
This is a future diagnostic step, NOT to be run until instructed after the
staging and recovery checks.
"""
from __future__ import annotations

from pathlib import Path
import os

from dock import inside, windows_devices
from psp_usb import identify, UsbTransitionError
from lab_eject import ppsspp_open, real_dock_open
from vsh_plugin_stage import ARM_TOKEN, CONFIG, ENTRY, STAGE_DIR, _sources, validate_source_files
from install_safe_probe import digest


def arm_observer(root: Path) -> Path:
    folder = inside(root, STAGE_DIR)
    cfg = inside(root, CONFIG)
    if not folder.is_dir() or not cfg.is_file():
        raise ValueError("Primero prepara PSPDockProbe DESACTIVADO.")
    text = cfg.read_bytes().replace(b"\r", b"").lower().split(b"\n")
    if text.count(ENTRY.rstrip(b"\n").lower()) != 1:
        raise ValueError("PSP Dock no figura exactamente una vez con el plugin en OFF.")
    for name, (_, expected) in _sources().items():
        file = folder / name
        if not file.is_file() or digest(file) != expected:
            raise ValueError("El plugin en PSP no coincide con el paquete validado: " + name)
    arm = folder / "ARM.TXT"
    if arm.exists():
        raise ValueError("El observador ya está armado; no se modifica.")
    if (folder / "BOOT.TXT").exists():
        raise ValueError("Existe una marca de una sesión anterior; no se crea otra ARM.")
    with arm.open("xb") as dest:
        dest.write(ARM_TOKEN)
        dest.flush()
        os.fsync(dest.fileno())
    if arm.read_bytes() != ARM_TOKEN:
        raise ValueError("La marca ARM se copió de forma incompleta.")
    return arm


def main() -> int:
    try:
        if ppsspp_open() or real_dock_open():
            raise ValueError("Cierra PPSSPP y el servicio PSP Dock antes de armar.")
        drives = windows_devices()
        if len(drives) != 1:
            raise ValueError("Se necesita una única PSP Sony en USB nativo.")
        root = Path(drives[0]["root"])
        ident, _ = identify(str(root))
        print("PSP:", ident.model, ident.drive)
        print("El plugin debe seguir OFF en PLUGINS.TXT.")
        print("Esto solo crea un marcador de consentimiento bajo SEPLUGINS/PSPDockProbe.")
        print("NO activa plugins ni USB. Requiere prueba de recuperación por START.")
        if input("Escribe ARM OBSERVER para preparar esta prueba: ").strip() != "ARM OBSERVER":
            print("Cancelado sin escritura.")
            return 0
        if identify(str(root))[0] != ident:
            raise ValueError("Identidad USB PSP modificada.")
        result = arm_observer(root)
        print("Marcador ARM verificado:", result)
        print("PLUGIN SIGUE DESACTIVADO. No lo habilites sin revisar la recuperación.")
        return 0
    except (OSError, ValueError, UsbTransitionError) as exc:
        print("ARM CANCELADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
