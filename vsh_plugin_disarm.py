"""Safely remove ONLY the exact VSH observer ARM marker after a test.

Unlike enabling ARM, disarming is also allowed if the plugin configuration
has since been changed or its PRX is damaged. This is deliberate: recovery
must not depend on the module still being healthy.

Only a verified Sony PSP volume may be used. The script does not modify
PLUGINS.TXT, BOOT.TXT, PRX modules, firmware, SAVEDATA or USB roles.
The optional BOOT.TXT remains as diagnostic evidence after disarming.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dock import inside, windows_devices
from lab_eject import ppsspp_open, real_dock_open
from psp_usb import identify, UsbTransitionError
from vsh_plugin_stage import ARM_TOKEN, STAGE_DIR


class DisarmError(ValueError):
    pass


def plan_disarm(psp_root: Path) -> dict:
    """Read-only inspection. Never delete an unknown or edited marker."""
    folder = inside(psp_root, STAGE_DIR)
    marker = inside(psp_root, STAGE_DIR + "/ARM.TXT")
    boot = inside(psp_root, STAGE_DIR + "/BOOT.TXT")
    if not folder.is_dir():
        raise DisarmError("No existe la carpeta PSPDockProbe esperada.")
    if not marker.exists():
        return {"marker": marker, "armed": False,
                "boot_marker_exists": boot.is_file()}
    if not marker.is_file() or marker.read_bytes() != ARM_TOKEN:
        raise DisarmError("ARM.TXT distinto del token conocido: no se elimina.")
    return {"marker": marker, "armed": True,
            "boot_marker_exists": boot.is_file()}


def disarm(psp_root: Path) -> dict:
    """Remove one known ARM.TXT, leaving all other PSP files unchanged."""
    report = plan_disarm(psp_root)
    if not report["armed"]:
        return {"changed": False, "armed": False,
                "boot_marker_preserved": report["boot_marker_exists"]}
    marker = report["marker"]
    # Verify again immediately before the sole mutation.
    if not marker.is_file() or marker.read_bytes() != ARM_TOKEN:
        raise DisarmError("ARM.TXT cambió; no se elimina.")
    marker.unlink()
    if marker.exists():
        raise DisarmError("ARM.TXT sigue presente: requiere revisión manual.")
    return {"changed": True, "armed": False,
            "boot_marker_preserved": report["boot_marker_exists"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Retirar ARM VSH sin tocar BOOT ni configuración")
    parser.add_argument("--run", action="store_true",
                        help="Requiere confirmación escrita para quitar el ARM exacto")
    args = parser.parse_args()
    try:
        if ppsspp_open() or real_dock_open():
            raise DisarmError("Cierra PPSSPP y PSP Dock real.")
        devices = windows_devices()
        if len(devices) != 1:
            raise DisarmError("Conecta exactamente una PSP Sony por USB nativo.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        plan = plan_disarm(root)
        print("PSP Sony:", identity.drive, identity.model)
        print("ARM del observador:", "PRESENTE" if plan["armed"] else "AUSENTE")
        print("BOOT.TXT:", "se conservará" if plan["boot_marker_exists"] else "ausente")
        if not plan["armed"]:
            print("No hay ARM que retirar; no se modifica nada.")
            return 0
        if not args.run:
            print("VISTA PREVIA: NO se ha eliminado ni escrito nada.")
            return 0
        if input("Escribe DESARMAR VSH para retirar SOLO ARM.TXT: ").strip() != "DESARMAR VSH":
            print("Cancelado sin cambios.")
            return 0
        if identify(str(root))[0] != identity:
            raise DisarmError("La identidad de la PSP cambió.")
        print("RESULTADO:", disarm(root))
        print("Puedes expulsar el almacenamiento normalmente desde Windows.")
        return 0
    except (OSError, UsbTransitionError, ValueError) as exc:
        print("DESARMADO CANCELADO:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
