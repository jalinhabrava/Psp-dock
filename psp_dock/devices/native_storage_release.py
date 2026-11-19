"""Release one verified native PSP medium while leaving its HID interface alive.

The only mutating route is LOCK -> DISMOUNT -> EJECT_MEDIA on one volume
handle. A failed lock never reaches dismount. There is no PnP removal, forced
dismount, OFFLINE, LOAD_MEDIA, service change, or implicit retry here.

Microsoft contracts used:
https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume
https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-fsctl_dismount_volume
https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-ioctl_storage_eject_media
https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-ioctl_storage_get_device_number
"""
from __future__ import annotations

from copy import deepcopy
import ctypes as C
from ctypes import wintypes as W
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import time
import uuid

from psp_dock.devices.psp_usb import PspStorage, UsbTransitionError, identify


FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020
IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
FILE_DEVICE_DISK = 7
_PARENT = re.compile(r'USB\\VID_054C&PID_01CD\\[^\\]+', re.I)
_GUID = re.compile(r'\\\\\?\\Volume\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}\\', re.I)


class StorageReleaseError(UsbTransitionError):
    """Carries actual progress, including an eject followed by journal failure."""

    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = deepcopy(receipt)
        self.storage_released = receipt.get('storage_released') is True


class _StorageDeviceNumber(C.Structure):
    # Fixed Windows DWORD widths also let host-only tests check the ABI.
    _fields_ = [('device_type', C.c_uint32), ('device_number', C.c_uint32),
                ('partition_number', C.c_uint32)]


class WindowsVolumeApi:
    """Small Win32 boundary; construction and calls require Windows."""

    def __init__(self):
        if os.name != 'nt':
            raise UsbTransitionError('La liberación del volumen requiere Windows.')
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        k = self.kernel
        k.GetVolumeNameForVolumeMountPointW.argtypes = [W.LPCWSTR, W.LPWSTR, W.DWORD]
        k.GetVolumeNameForVolumeMountPointW.restype = W.BOOL
        k.GetVolumeInformationW.argtypes = [W.LPCWSTR, W.LPWSTR, W.DWORD,
            C.POINTER(W.DWORD), C.POINTER(W.DWORD), C.POINTER(W.DWORD), W.LPWSTR, W.DWORD]
        k.GetVolumeInformationW.restype = W.BOOL
        k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID,
                                  W.DWORD, W.DWORD, W.HANDLE]
        k.CreateFileW.restype = W.HANDLE
        k.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, W.LPVOID, W.DWORD,
                                       W.LPVOID, W.DWORD, C.POINTER(W.DWORD), W.LPVOID]
        k.DeviceIoControl.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]
        k.CloseHandle.restype = W.BOOL

    def topology(self, drive):
        # Values enter via an environment variable, never interpolated code.
        # Enumerating ALL partitions/logical volumes prevents a whole-medium
        # eject from affecting a second volume that the Dock did not verify.
        script = r'''
$ErrorActionPreference='Stop'
$letter=$env:PSP_DOCK_RELEASE_DRIVE
$logical=@(Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='"+$letter+"'"))
if ($logical.Count -ne 1) { throw 'Logical volume missing or ambiguous' }
$selected=@(Get-CimAssociatedInstance -InputObject $logical[0] -Association Win32_LogicalDiskToPartition)
if ($selected.Count -ne 1) { throw 'Partition mapping ambiguous' }
$disks=@(Get-CimAssociatedInstance -InputObject $selected[0] -Association Win32_DiskDriveToDiskPartition)
if ($disks.Count -ne 1) { throw 'Physical disk mapping ambiguous' }
$disk=$disks[0]
$parts=@(Get-CimAssociatedInstance -InputObject $disk -Association Win32_DiskDriveToDiskPartition)
$volumes=@(foreach ($part in $parts) {
    Get-CimAssociatedInstance -InputObject $part -Association Win32_LogicalDiskToPartition
})
[pscustomobject]@{
    disk_id=[string]$disk.PNPDeviceID; disk_number=[int]$disk.Index
    disk_device=[string]$disk.DeviceID; partition_count=$parts.Count
    selected_partition=[string]$selected[0].DeviceID
    partitions=@($parts | ForEach-Object { [string]$_.DeviceID })
    logical_volumes=@($volumes | ForEach-Object { [string]$_.DeviceID })
} | ConvertTo-Json -Depth 4 -Compress
'''
        completed = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            env={**os.environ, 'PSP_DOCK_RELEASE_DRIVE':drive[:2]},
            capture_output=True, text=True, timeout=30, creationflags=0x08000000)
        if completed.returncode:
            raise UsbTransitionError('No se pudo verificar el disco completo de la PSP: ' +
                                     (completed.stderr.strip()[:400] or 'error CIM'))
        try:
            return json.loads(completed.stdout)
        except (ValueError, TypeError) as exc:
            raise UsbTransitionError('La relación de volúmenes no es válida.') from exc

    def volume_guid(self, drive):
        value = C.create_unicode_buffer(64)
        if not self.kernel.GetVolumeNameForVolumeMountPointW(drive, value, len(value)):
            raise C.WinError(C.get_last_error())
        return value.value

    def volume_serial(self, guid):
        serial = W.DWORD()
        if not self.kernel.GetVolumeInformationW(guid, None, 0, C.byref(serial), None, None, None, 0):
            raise C.WinError(C.get_last_error())
        return str(serial.value)

    def media_status(self, guid):
        """Metadata only, after unlock/close; never open PSP directories."""
        serial = W.DWORD()
        if self.kernel.GetVolumeInformationW(guid, None, 0, C.byref(serial), None, None, None, 0):
            return {'available':True, 'volume_serial':str(serial.value), 'winerror':0}
        error = C.get_last_error()
        return {'available':False if error in (21, 1112) else None, 'winerror':error}

    def open_volume(self, guid):
        handle = self.kernel.CreateFileW(guid[:-1], 0xC0000000, 3, None, 3, 0, None)
        if handle is None or handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        return handle

    def device_number(self, handle):
        value, returned = _StorageDeviceNumber(), W.DWORD()
        if not self.kernel.DeviceIoControl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER,
                None, 0, C.byref(value), C.sizeof(value), C.byref(returned), None):
            raise C.WinError(C.get_last_error())
        if returned.value != C.sizeof(value):
            raise UsbTransitionError('Windows devolvió una identidad de disco incompleta.')
        return {name:int(getattr(value, name)) for name, _ in value._fields_}

    def control(self, handle, code):
        returned = W.DWORD()
        if not self.kernel.DeviceIoControl(handle, code, None, 0, None, 0, C.byref(returned), None):
            raise C.WinError(C.get_last_error())

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise C.WinError(C.get_last_error())


def _native_parent(instance):
    # Deferred import avoids a cycle when NativeDockTransport imports us.
    from psp_dock.devices.native_dock import composite_parent
    return composite_parent(instance)


def _check_topology(topology, identity):
    if not isinstance(topology, dict):
        raise UsbTransitionError('No se ha identificado el disco de la PSP.')
    number = topology.get('disk_number')
    if (type(number) is not int or number < 0 or
            str(topology.get('disk_id', '')).casefold() != identity.disk_id.casefold() or
            str(topology.get('disk_device', '')).casefold() != rf'\\.\PhysicalDrive{number}'.casefold()):
        raise UsbTransitionError('El disco físico ya no corresponde a la PSP verificada.')
    partitions, volumes = topology.get('partitions'), topology.get('logical_volumes')
    if (type(topology.get('partition_count')) is not int or topology['partition_count'] != 1 or
            not isinstance(partitions, list) or len(partitions) != 1 or
            not isinstance(partitions[0], str) or not partitions[0] or
            partitions[0] != topology.get('selected_partition') or
            not isinstance(volumes, list) or len(volumes) != 1 or
            not isinstance(volumes[0], str) or volumes[0].casefold() != identity.drive[:2].casefold()):
        raise UsbTransitionError('La expulsión requiere un único volumen y una única partición de esta PSP.')
    return number


def _check_number(number, expected_disk):
    if (not isinstance(number, dict) or
            any(type(number.get(key)) is not int for key in
                ('device_type', 'device_number', 'partition_number')) or
            number['device_type'] != FILE_DEVICE_DISK or number['device_number'] != expected_disk or
            not 0 < number['partition_number'] < 0xFFFFFFFF):
        raise UsbTransitionError('El handle abierto no pertenece a la partición PSP verificada.')


def validate_media_release_receipt(receipt, *, require_absent=False):
    """Validate recorded evidence without accessing a device or filesystem."""
    if (not isinstance(receipt, dict) or
            receipt.get('schema') != 'pspdock-native-storage-release-v1' or
            receipt.get('eject_kind') != 'media' or receipt.get('host_eject_accepted') is not True or
            not isinstance(receipt.get('volume_guid'), str) or not _GUID.fullmatch(receipt['volume_guid']) or
            not isinstance(receipt.get('steps'), list)):
        raise UsbTransitionError('No hay una confirmación registrada de expulsión de este medio.')
    expected = [('lock_volume', FSCTL_LOCK_VOLUME), ('dismount_volume', FSCTL_DISMOUNT_VOLUME),
                ('eject_media', IOCTL_STORAGE_EJECT_MEDIA)]
    steps = receipt['steps']
    if (len(steps) != len(expected) or any(not isinstance(step, dict) or
            (step.get('operation'), step.get('code')) != operation or step.get('ok') is not True
            for step, operation in zip(steps, expected))):
        raise UsbTransitionError('No están confirmados el bloqueo, desmontaje y expulsión del mismo volumen.')
    if require_absent:
        check = receipt.get('release_check')
        if (receipt.get('storage_released') is not True or not isinstance(check, dict) or
                check.get('media_absent') is not True or check.get('available') is not False or
                type(check.get('winerror')) is not int or check['winerror'] not in (21, 1112) or
                check.get('volume_guid') != receipt['volume_guid']):
            raise UsbTransitionError('Falta la comprobación de medio ausente del volumen expulsado.')


def verify_media_released(receipt, *, api=None) -> dict:
    """Read-only reconciliation of an EJECT whose actual success was captured.

    The caller must separately retain/check HID nonce, build and USB parent.
    A crash during an unconfirmed EJECT is not converted into success merely
    because a later metadata read finds no medium.
    """
    validate_media_release_receipt(receipt)
    boundary = api if api is not None else WindowsVolumeApi()
    check = boundary.media_status(receipt['volume_guid'])
    if not isinstance(check, dict):
        raise UsbTransitionError('Windows no devolvió un estado verificable del medio.')
    absent = (check.get('available') is False and type(check.get('winerror')) is int and
              check['winerror'] in (21, 1112))
    return {'media_absent':absent, 'winerror':check.get('winerror'),
            'available':check.get('available'), 'volume_guid':receipt['volume_guid']}


def release_native_storage(identity: PspStorage, parent: tuple[int, str], *,
                           checkpoint, api=None, identify_storage=identify,
                           parent_of=_native_parent) -> dict:
    """Safely eject media; caller separately closes the same firmware session.

    `checkpoint` must durably store each supplied record or raise. It is called
    before LOCK and after actual results. Exceptions carry `.receipt`; if
    `.storage_released` is true, only shutdown reconciliation is permitted,
    never another filesystem scan or automatic media reload.
    """
    if not callable(checkpoint):
        raise TypeError('Se necesita un registro persistente para cerrar el volumen.')
    record = {'schema':'pspdock-native-storage-release-v1', 'operation_id':uuid.uuid4().hex,
              'utc':datetime.now(timezone.utc).isoformat(), 'status':'checking',
              'eject_kind':'media', 'storage_released':False,
              'host_eject_accepted':False, 'steps':[], 'cleanup':[]}
    handle, locked, failure = None, False, None
    stage = 'identity'
    boundary = api

    def save(status):
        record['status'] = status
        checkpoint(deepcopy(record))

    def operation(name, code, *, cleanup=False):
        started = time.perf_counter()
        row = {'operation':name, 'code':code}
        try:
            boundary.control(handle, code)
            row['ok'] = True
        except Exception as exc:
            row.update(ok=False, error=str(exc), winerror=getattr(exc, 'winerror', None))
            raise
        finally:
            row['duration_ms'] = round((time.perf_counter()-started)*1000, 3)
            record['cleanup' if cleanup else 'steps'].append(row)

    try:
        if (not isinstance(identity, PspStorage) or not isinstance(parent, tuple) or len(parent) != 2 or
                type(parent[0]) is not int or parent[0] <= 0 or not isinstance(parent[1], str) or
                not _PARENT.fullmatch(parent[1])):
            raise UsbTransitionError('La liberación requiere una PSP nativa ya vinculada.')
        record.update(identity=asdict(identity), usb_parent=parent[1], parent_devinst=parent[0])
        fresh, _ = identify_storage(identity.drive)
        if fresh != identity or parent_of(fresh.disk_id) != parent:
            raise UsbTransitionError('La PSP o la tarjeta cambiaron antes del cierre.')
        boundary = api if api is not None else WindowsVolumeApi()
        stage = 'topology'
        topology = boundary.topology(identity.drive)
        expected_disk = _check_topology(topology, identity)
        record['topology'] = deepcopy(topology)
        guid = boundary.volume_guid(identity.drive)
        if not isinstance(guid, str) or not _GUID.fullmatch(guid):
            raise UsbTransitionError('Windows no publicó una ruta de volumen válida.')
        if boundary.volume_serial(guid) != identity.volume_serial:
            raise UsbTransitionError('El volumen ya no contiene la misma tarjeta.')
        record['volume_guid'] = guid
        stage = 'open_volume'
        handle = boundary.open_volume(guid)
        number = boundary.device_number(handle)
        _check_number(number, expected_disk)
        record['device_number'] = deepcopy(number)
        if (boundary.volume_guid(identity.drive).casefold() != guid.casefold() or
                parent_of(identity.disk_id) != parent):
            raise UsbTransitionError('La asociación de la unidad cambió antes de bloquearla.')
        stage = 'persist_intent'
        save('release_requested')  # Required before the first mutating IOCTL.
        stage = 'lock_volume'
        operation(stage, FSCTL_LOCK_VOLUME)
        locked = True
        # No filesystem/CIM reads after LOCK: use this same handle and PnP.
        if boundary.device_number(handle) != number or parent_of(identity.disk_id) != parent:
            raise UsbTransitionError('La identidad cambió durante el bloqueo del volumen.')
        save('volume_locked')
        stage = 'dismount_volume'
        operation(stage, FSCTL_DISMOUNT_VOLUME)
        save('volume_dismounted')
        stage = 'persist_eject_intent'
        record['pending_operation'] = 'eject_media'
        save('eject_requested')
        stage = 'eject_media'
        try:
            operation(stage, IOCTL_STORAGE_EJECT_MEDIA)
        finally:
            record.pop('pending_operation', None)
        record['host_eject_accepted'] = True
        save('media_eject_accepted')
    except Exception as exc:
        failure = exc
        record['error'] = {'stage':stage, 'message':str(exc), 'winerror':getattr(exc, 'winerror', None)}
    finally:
        if handle is not None:
            if locked:
                try:
                    operation('unlock_volume', FSCTL_UNLOCK_VOLUME, cleanup=True)
                except Exception:
                    pass  # Closing the handle also releases its volume lock.
            try:
                boundary.close(handle)
                record['cleanup'].append({'operation':'close_handle', 'ok':True})
            except Exception as exc:
                record['cleanup'].append({'operation':'close_handle', 'ok':False,
                    'error':str(exc), 'winerror':getattr(exc, 'winerror', None)})
        record['cleanup_complete'] = all(item['ok'] for item in record['cleanup'])
    if record['host_eject_accepted']:
        try:
            check = verify_media_released(record, api=boundary)
            record['release_check'] = deepcopy(check)
            if not check['media_absent']:
                raise UsbTransitionError('Windows aún no confirmó que el medio expulsado esté inaccesible.')
            record['storage_released'] = True
        except Exception as exc:
            record['release_check_error'] = str(exc)
            if failure is None:
                failure = exc
    try:
        save('media_ejected' if record['storage_released'] else 'failed')
    except Exception as exc:
        record['checkpoint_error'] = str(exc)
        if failure is None:
            failure = exc
    if failure is not None:
        message = ('El medio está expulsado, pero no se pudo completar su registro de cierre.'
                   if record['storage_released'] else
                   'No se ha liberado el almacenamiento de la PSP: ' + str(failure))
        raise StorageReleaseError(message, record) from failure
    return deepcopy(record)
