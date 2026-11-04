"""Read-only status inspection of a staged/armed PSP Dock VSH observer.

The BOOT.TXT marker is DIAGNOSTIC ONLY. Since it is stored on a filesystem,
it can be stale. It must NEVER authorize USB eject, HID, game launching,
or save synchronization; the future bridge needs real-time authenticated
role acknowledgment over its own USB endpoint.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

from dock import inside, windows_devices
from psp_dock.devices.psp_usb import identify, UsbTransitionError
from tools.psp.vsh_plugin_stage import CONFIG, STAGE_DIR, ENTRY, ARM_TOKEN, FILES
from tools.psp.install_safe_probe import digest


_HEX8 = re.compile(r"^[0-9A-F]{8}$")


def parse_boot_marker(data: bytes) -> dict:
    try:
        decoded = data.decode("ascii", "strict")
    except UnicodeError as exc:
        raise ValueError("Marcador VSH no ASCII") from exc
    lines = decoded.splitlines()
    if len(lines) != 5:
        raise ValueError("Marcador VSH sin cinco líneas exactas.")
    if lines[0] != "PSPDOCK_VSH_OBSERVER=01" or lines[4] != "FIRMWARE_UNCHANGED=1":
        raise ValueError("Versión de marcador VSH desconocida.")
    result = {}
    for prefix, line in zip(("USB", "BUS", "STORAGE"), lines[1:4]):
        if not line.startswith(prefix + "="):
            raise ValueError("Campo del marcador VSH incorrecto.")
        value = line[len(prefix) + 1:]
        if not _HEX8.fullmatch(value):
            raise ValueError("Hexadecimal VSH malformado.")
        result[prefix.lower()] = int(value, 16)
    if result["usb"] & (0x200 | 0x020 | 0x002):
        raise ValueError("Marcador inesperado: USB activado o cable conectado.")
    return result


def inspect_volume(psp_root: Path) -> dict:
    config = inside(psp_root, CONFIG)
    folder = inside(psp_root, STAGE_DIR)
    config_bytes = config.read_bytes() if config.is_file() else b""
    entry_bytes = ENTRY.rstrip(b"\n").lower()
    on_bytes = entry_bytes.replace(b", off", b", on")
    normalized = config_bytes.replace(b"\r", b"").lower().split(b"\n")
    off_count, on_count = normalized.count(entry_bytes), normalized.count(on_bytes)
    staged = folder.is_dir()
    marker = folder / "BOOT.TXT"
    integrity = {}
    for filename, (_, expected_hash) in FILES.items():
        candidate = folder / filename
        integrity[filename] = candidate.is_file() and digest(candidate) == expected_hash
    report = {
        "staged": staged,
        "disabled_entry_present": off_count == 1 and on_count == 0,
        "enabled_entry_present": on_count == 1 and off_count == 0,
        "plugin_config": ("OFF" if off_count == 1 and on_count == 0 else
                          "ON" if on_count == 1 and off_count == 0 else
                          "UNKNOWN"),
        "known_prx_integrity": integrity,
        "vsh_prx_present": (folder / "pspdockvsh.prx").is_file(),
        "helper_prx_present": (folder / "usbstate.prx").is_file(),
        "armed": (folder / "ARM.TXT").is_file() and
                 (folder / "ARM.TXT").read_bytes() == ARM_TOKEN,
        "boot_marker_exists": marker.is_file(),
        "boot_marker_warning": "Registro de archivo; puede ser antiguo. NO es ACK USB.",
        "boot_marker": None,
    }
    if marker.is_file():
        try:
            report["boot_marker"] = parse_boot_marker(marker.read_bytes())
        except ValueError as error:
            report["boot_marker_warning"] = str(error)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Estado VSH de PSP Dock, solo lectura")
    parser.parse_args()
    try:
        devices = windows_devices()
        if len(devices) != 1:
            raise ValueError("Conecta una única PSP Sony por USB nativo.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        status = inspect_volume(root)
        print("PSP verificada:", identity.drive, identity.model)
        for key, value in status.items():
            print(f"{key}: {value}")
        print("Solo diagnóstico; esta lectura no permite cambiar modos USB.")
        return 0
    except (OSError, UsbTransitionError, ValueError) as error:
        print("LECTURA CANCELADA:", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
