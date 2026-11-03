"""Narrowly scoped PSP USB storage detection and *safe* Windows PnP eject.

Never force-dismount a volume, change firmware, or interpret the host's success
as evidence that PSP HID is active. All file operations must finish first.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess


class UsbTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PspStorage:
    drive: str
    volume_serial: str
    disk_id: str
    usb_device_id: str
    model: str


_DRIVE_RE = re.compile(r"^[A-Z]:\\$", re.I)
_USB_RE = re.compile(r"^USB\\VID_054C&PID_[0-9A-F]{4}(?:&|\\)", re.I)
_DISK_RE = re.compile(r"^USBSTOR\\DISK&VEN_SONY&PROD_", re.I)


def validate_identity(drive: str, serial: str, model: str,
                      disk_id: str, usb_id: str) -> PspStorage:
    """Strict association: a generic USB stick must never be ejected."""
    if not _DRIVE_RE.fullmatch(drive) or not str(serial).isdigit():
        raise UsbTransitionError("Unidad o identificador de volumen inválido.")
    if "PSP" not in model.upper() or not _DISK_RE.match(disk_id):
        raise UsbTransitionError("El disco no está identificado como SONY PSP.")
    if not _USB_RE.match(usb_id):
        raise UsbTransitionError("No se ha identificado el padre USB Sony de la PSP.")
    return PspStorage(drive.upper(), str(serial), disk_id, usb_id, model)


def _volume_serial(drive: str) -> str:
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumeInformationW.argtypes = [
        W.LPCWSTR, W.LPWSTR, W.DWORD, C.POINTER(W.DWORD), C.POINTER(W.DWORD),
        C.POINTER(W.DWORD), W.LPWSTR, W.DWORD]
    kernel.GetVolumeInformationW.restype = W.BOOL
    serial = W.DWORD()
    if not kernel.GetVolumeInformationW(drive, None, 0, C.byref(serial), None, None, None, 0):
        raise UsbTransitionError(f"Error leyendo serial de volumen: {C.WinError(C.get_last_error())}")
    return str(serial.value)


def _physical_disk(drive: str) -> tuple[str, str]:
    # Fresh native association avoids launching PowerShell for every guard.
    # The compatibility fallback performs the same independent CIM binding.
    from windows_storage_identity import physical_disk
    try:
        return physical_disk(drive)
    except (OSError, ValueError):
        pass
    # No user-supplied paths are interpolated into the PowerShell program.
    # CIM associations bind the exact logical volume to one physical disk.
    script = r'''
$ErrorActionPreference="Stop"
$letter=$env:PSP_DOCK_SELECTED_DRIVE
$logical=@(Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + $letter + "'"))
if ($logical.Count -ne 1) { throw "Logical volume missing or ambiguous" }
$parts=@(Get-CimAssociatedInstance -InputObject $logical[0] -Association Win32_LogicalDiskToPartition)
if ($parts.Count -ne 1) { throw "Partition mapping ambiguous" }
$disks=@(Get-CimAssociatedInstance -InputObject $parts[0] -Association Win32_DiskDriveToDiskPartition)
if ($disks.Count -ne 1) { throw "Physical disk mapping ambiguous" }
[pscustomobject]@{id=$disks[0].PNPDeviceID; model=$disks[0].Model} | ConvertTo-Json -Compress
'''
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "PSP_DOCK_SELECTED_DRIVE": drive[:2]},
        capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    if completed.returncode:
        raise UsbTransitionError("No se ha podido asociar la PSP a un disco único: " +
                                 (completed.stderr.strip()[:400] or "error CIM"))
    try:
        obj = json.loads(completed.stdout)
        return str(obj["id"]), str(obj["model"])
    except (ValueError, TypeError, KeyError) as exc:
        raise UsbTransitionError("Respuesta CIM no reconocida.") from exc


def _usb_parent(disk_id: str) -> tuple[int, str]:
    cfg = C.WinDLL("cfgmgr32", use_last_error=True)
    cfg.CM_Locate_DevNodeW.argtypes = [C.POINTER(W.DWORD), W.LPWSTR, W.ULONG]
    cfg.CM_Locate_DevNodeW.restype = W.DWORD
    cfg.CM_Get_Parent.argtypes = [C.POINTER(W.DWORD), W.DWORD, W.ULONG]
    cfg.CM_Get_Parent.restype = W.DWORD
    cfg.CM_Get_Device_IDW.argtypes = [W.DWORD, W.LPWSTR, W.ULONG, W.ULONG]
    cfg.CM_Get_Device_IDW.restype = W.DWORD
    current = W.DWORD()
    if cfg.CM_Locate_DevNodeW(C.byref(current), disk_id, 0):
        raise UsbTransitionError("Dispositivo de almacenamiento ya desconectado.")
    for _ in range(8):
        parent = W.DWORD()
        if cfg.CM_Get_Parent(C.byref(parent), current, 0):
            break
        name = C.create_unicode_buffer(1024)
        if cfg.CM_Get_Device_IDW(parent, name, len(name), 0):
            raise UsbTransitionError("No se pudo leer la identidad USB padre.")
        if _USB_RE.match(name.value):
            return int(parent.value), name.value
        current = parent
    raise UsbTransitionError("No se encontró el padre USB de la PSP.")


def identify(drive: str) -> tuple[PspStorage, int]:
    if os.name != "nt":
        raise UsbTransitionError("Se requiere Windows.")
    if not _DRIVE_RE.fullmatch(drive):
        raise UsbTransitionError("Indica una raíz de disco, por ejemplo G:\\")
    drive = drive.upper()
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [W.LPCWSTR]
    kernel.GetDriveTypeW.restype = W.UINT
    if kernel.GetDriveTypeW(drive) != 2 or not (Path(drive) / "PSP").is_dir():
        raise UsbTransitionError("No es una unidad PSP extraíble montada.")
    serial = _volume_serial(drive)
    disk_id, model = _physical_disk(drive)
    devinst, usb_id = _usb_parent(disk_id)
    if _volume_serial(drive) != serial:
        raise UsbTransitionError('La Memory Stick cambió durante la identificación.')
    return validate_identity(drive, serial, model, disk_id, usb_id), devinst


def safe_eject(drive: str, expected_serial: str) -> dict:
    """No force: CR_REMOVE_VETOED is returned with owner and remains a failure."""
    first, devinst = identify(drive)
    if first.volume_serial != str(expected_serial):
        raise UsbTransitionError("La Memory Stick cambió: expulsión cancelada.")
    if identify(drive)[0] != first:
        raise UsbTransitionError("Identidad de PSP cambió antes de la expulsión.")
    cfg = C.WinDLL("cfgmgr32", use_last_error=True)
    cfg.CM_Request_Device_EjectW.argtypes = [
        W.DWORD, C.POINTER(W.ULONG), W.LPWSTR, W.ULONG, W.ULONG]
    cfg.CM_Request_Device_EjectW.restype = W.DWORD
    veto = W.ULONG()
    name = C.create_unicode_buffer(1024)
    result = int(cfg.CM_Request_Device_EjectW(devinst, C.byref(veto), name, len(name), 0))
    return {
        "host_eject_accepted": result == 0, "configret": result,
        "veto_type": int(veto.value), "veto_name": name.value,
        "drive": first.drive, "volume_serial": first.volume_serial,
        "usb_device_id": first.usb_device_id, "psp_hid_confirmed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verificación y expulsión segura PSP")
    parser.add_argument("--drive", required=True, help="Por ejemplo G:\\")
    parser.add_argument("--eject", action="store_true", help="Solo solicitud de expulsión segura")
    parser.add_argument("--serial", help="Serial de la misma tarjeta; obligatorio con --eject")
    args = parser.parse_args()
    try:
        if args.eject:
            if not args.serial:
                parser.error("La expulsión requiere --serial")
            result = safe_eject(args.drive, args.serial)
        else:
            result = asdict(identify(args.drive)[0])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not args.eject or result["host_eject_accepted"] else 2
    except (UsbTransitionError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
