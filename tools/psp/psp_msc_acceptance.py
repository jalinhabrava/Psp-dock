"""Read-only physical acceptance for the synthetic PSP MSC + HID proof.

Windows must mount the synthetic FAT12 disk with its inbox USBSTOR driver.
Only PROBE.DAT and SESSION.BIN are opened. No raw disk, SCSI commands,
driver installation, eject, real Memory Stick access, or writes to the PSP.
Unbuffered file reads keep this test from mistaking Windows file-cache
hits for simultaneous USB storage traffic and gamepad input.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from tools.psp.psp_composite_acceptance import _joystick_sample, matching_hid_session
from psp_dock.devices.psp_composite_client import PROBE_SIZE, PROBE_SHA256, _new_local_destination
from psp_dock.devices.psp_winusb_transport import WinUSBError
from psp_dock.devices.bridge_hid import MSC_TEST_PID

MSC_REVISION = 0x0207
MSC_PRODUCT = MSC_TEST_PID
VOLUME_LABEL = 'PSPDOCKTEST'
VOLUME_SERIAL = '50444334'
COMPOSITE_PARENT = re.compile(r'^USB\\VID_054C&PID_01CC\\[^\\]+$', re.I)
MSC_INTERFACE = re.compile(r'^USB\\VID_054C&PID_01CC&MI_01\\[^\\]+$', re.I)
HID_INTERFACE = re.compile(r'^USB\\VID_054C&PID_01CC&MI_00\\[^\\]+$', re.I)
MSC_DISK = re.compile(r'^USBSTOR\\DISK&VEN_PSPDOCK&PROD_MSC_HID_TEST(?:&|\\)', re.I)

DISCOVER_SCRIPT = r'''
$ErrorActionPreference='Stop'
$keys=@('DEVPKEY_Device_Parent','DEVPKEY_Device_Service','DEVPKEY_Device_HardwareIds',
        'DEVPKEY_Device_ProblemCode','DEVPKEY_Device_LocationPaths')
$devices=@(Get-PnpDevice -PresentOnly -Class 'USB','HIDClass','DiskDrive' | Where-Object {
    $_.InstanceId -match '^(USB|HID)\\VID_054C&PID_01CC(&|\\)' -or
    $_.InstanceId -match '^USBSTOR\\DISK&VEN_PSPDOCK&PROD_MSC_HID_TEST(&|\\)' -or
    $_.InstanceId -match '^USB\\(VID_0000&PID_000[02]|UNKNOWN)'
})
$byId=@{}
$nodes=@(foreach($device in $devices) {
    $props=@{}
    $values=@(Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName $keys -ErrorAction SilentlyContinue)
    foreach($p in $values) {if($null -ne $p) {$props[$p.KeyName]=$p.Data}}
    $node=[PSCustomObject]@{instance=$device.InstanceId;status=$device.Status;class=$device.Class;
        parent=$props['DEVPKEY_Device_Parent'];service=$props['DEVPKEY_Device_Service'];
        hardware_ids=@($props['DEVPKEY_Device_HardwareIds']);
        problem=$props['DEVPKEY_Device_ProblemCode'];location_paths=@($props['DEVPKEY_Device_LocationPaths']);
        unidentified_usb_failure=($device.InstanceId -match '^USB\\(VID_0000&PID_000[02]|UNKNOWN)')}
    $byId[$device.InstanceId]=$node
    $node
})
function Get-DeviceChain([string]$id) {
    $seen=@{}
    for($depth=0; $depth -lt 6 -and $id -and -not $seen.ContainsKey($id) -and $byId.ContainsKey($id); $depth++) {
        $seen[$id]=$true
        $node=$byId[$id]
        $node
        if($id -match '^USB\\VID_054C&PID_01CC\\[^\\]+$') {break}
        $id=$node.parent
    }
}
$hids=@($nodes | Where-Object {
    $_.instance -match '^HID\\VID_054C&PID_01CC&MI_00(&|\\)'
} | ForEach-Object {
    [PSCustomObject]@{instance=$_.instance;chain=@(Get-DeviceChain $_.instance)}
})
$disks=@($nodes | Where-Object {$_.instance -match '^USBSTOR\\DISK&VEN_PSPDOCK&PROD_MSC_HID_TEST(&|\\)'})
$volumes=@()
$inventory=[ordered]@{queried=$false;pnp_disk_count=$disks.Count;hid_pnp_count=$hids.Count;
    skipped_reason='';disks=@();errors=@()}
function New-QueryError([string]$stage,[string]$instance,$exception) {
    $message=[string]$exception.Message
    if($message.Length -gt 240) {$message=$message.Substring(0,240)}
    [PSCustomObject]@{stage=$stage;instance=$instance;error=$message}
}
# No disk/partition WMI traversal until both function stacks are present.
# Parent/MI01 failures are still returned immediately in usb_nodes.
if($disks.Count -gt 0 -and $hids.Count -gt 0) {
    $inventory.queried=$true
    $cimDisks=@()
    try {
        $cimDisks=@(Get-CimInstance Win32_DiskDrive -ErrorAction Stop | Where-Object {
            $_.PNPDeviceID -match '^USBSTOR\\DISK&VEN_PSPDOCK&PROD_MSC_HID_TEST(&|\\)'
        })
    } catch {
        $inventory.errors+=New-QueryError 'disk' '' $_.Exception
    }
    foreach($disk in $cimDisks) {
        $diskRow=[ordered]@{pnp_id=$disk.PNPDeviceID;device_id=$disk.DeviceID;index=$disk.Index;
            size=$disk.Size;bytes_per_sector=$disk.BytesPerSector;media_loaded=$disk.MediaLoaded;
            status=$disk.Status;reported_partition_count=$disk.Partitions;partitions=@()}
        $chain=@(Get-DeviceChain $disk.PNPDeviceID)
        $parts=@()
        try {
            $parts=@(Get-CimAssociatedInstance -InputObject $disk -Association Win32_DiskDriveToDiskPartition -ErrorAction Stop)
        } catch {
            $inventory.errors+=New-QueryError 'partitions' $disk.PNPDeviceID $_.Exception
        }
        foreach($part in $parts) {
            $partRow=[ordered]@{device_id=$part.DeviceID;disk_index=$part.DiskIndex;index=$part.Index;
                starting_offset=$part.StartingOffset;size=$part.Size;type=$part.Type;
                boot_partition=$part.BootPartition;logical_disks=@()}
            $logical=@()
            try {
                $logical=@(Get-CimAssociatedInstance -InputObject $part -Association Win32_LogicalDiskToPartition -ErrorAction Stop)
            } catch {
                $inventory.errors+=New-QueryError 'logical_disks' $part.DeviceID $_.Exception
            }
            foreach($volume in $logical) {
                $partRow.logical_disks+=[PSCustomObject]@{device_id=$volume.DeviceID;
                    label=$volume.VolumeName;serial=$volume.VolumeSerialNumber;
                    filesystem=$volume.FileSystem;size=$volume.Size;drive_type=$volume.DriveType}
                $volumes+=[PSCustomObject]@{root=($volume.DeviceID+'\');label=$volume.VolumeName;
                    serial=$volume.VolumeSerialNumber;filesystem=$volume.FileSystem;
                    disk_pnp_id=$disk.PNPDeviceID;disk_index=$disk.Index;chain=$chain}
            }
            $diskRow.partitions+=[PSCustomObject]$partRow
        }
        $inventory.disks+=[PSCustomObject]$diskRow
    }
} elseif($disks.Count -eq 0) {
    $inventory.skipped_reason='waiting_pnp_disk'
} else {
    $inventory.skipped_reason='waiting_hid'
}
# These candidates are diagnostic only. A matching label/serial does not
# identify the USB parent and must NEVER become an acceptance volume.
# The caller requests this slower inventory at most once every 15 seconds.
$globalInventory=[ordered]@{queried=$false;parent_correlation='none';
    logical_disks=@();volumes=@();errors=@()}
if($includeGlobalVolumes -and $disks.Count -gt 0 -and $volumes.Count -eq 0) {
    $globalInventory.queried=$true
    try {
        $globalInventory.logical_disks=@(Get-CimInstance Win32_LogicalDisk -OperationTimeoutSec 5 -ErrorAction Stop |
            Where-Object {$_.VolumeName -eq 'PSPDOCKTEST' -or $_.VolumeSerialNumber -eq '50444334'} |
            ForEach-Object {[PSCustomObject]@{device_id=$_.DeviceID;label=$_.VolumeName;
                serial=$_.VolumeSerialNumber;filesystem=$_.FileSystem;size=$_.Size;drive_type=$_.DriveType}})
    } catch {
        $globalInventory.errors+=New-QueryError 'global_logical_disks' '' $_.Exception
    }
    try {
        $globalInventory.volumes=@(Get-CimInstance Win32_Volume -OperationTimeoutSec 5 -ErrorAction Stop |
            Where-Object {$_.Label -eq 'PSPDOCKTEST' -or $_.SerialNumber -eq 0x50444334} |
            ForEach-Object {[PSCustomObject]@{device_id=$_.DeviceID;drive_letter=$_.DriveLetter;
                label=$_.Label;serial=$_.SerialNumber;filesystem=$_.FileSystem;
                capacity=$_.Capacity;drive_type=$_.DriveType}})
    } catch {
        $globalInventory.errors+=New-QueryError 'global_volumes' '' $_.Exception
    }
}
ConvertTo-Json -InputObject @{supported=$true;backend='windows-cim';volumes=$volumes;hid_nodes=$hids;usb_nodes=$nodes;
    storage_inventory=$inventory;uncorrelated_volume_inventory=$globalInventory} -Depth 10 -Compress
'''


def discovery_stage(snapshot: dict) -> str:
    """Describe a missing association without treating global volumes as proof."""
    inventory = snapshot.get('storage_inventory')
    if not isinstance(inventory, dict):
        return 'not_recorded'
    if inventory.get('errors'):
        return 'cim_query_error'
    if snapshot.get('volumes'):
        return 'linked_logical_volume'
    if not inventory.get('pnp_disk_count'):
        return 'waiting_pnp_disk'
    if not inventory.get('queried'):
        return inventory.get('skipped_reason') or 'cim_not_queried'
    disks = inventory.get('disks', [])
    if not disks:
        return 'pnp_disk_without_cim_disk'
    if not any(disk.get('partitions') for disk in disks):
        return 'cim_disk_without_partition'
    return 'partition_without_logical_disk'


def discover_volumes(*, run=subprocess.run, include_global_volumes: bool = False) -> dict:
    if type(include_global_volumes) is not bool:
        raise ValueError('La opción de inventario global debe ser booleana.')
    if os.name != 'nt':
        return {'supported': False, 'backend': 'unavailable', 'volumes': [], 'hid_nodes': [], 'usb_nodes': []}
    script = '$includeGlobalVolumes=' + ('$true' if include_global_volumes else '$false') + '\n' + DISCOVER_SCRIPT
    completed = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                    capture_output=True, text=True, timeout=45, creationflags=0x08000000)
    if completed.returncode:
        raise WinUSBError('No se pudo correlacionar el volumen USB: ' + completed.stderr[:240])
    if len(completed.stdout) > 192 * 1024:
        raise WinUSBError('La consulta USB devolvió demasiados datos.')
    data = json.loads(completed.stdout.lstrip('\ufeff'))
    if (not isinstance(data, dict) or data.get('supported') is not True or
            data.get('backend') != 'windows-cim' or
            not isinstance(data.get('volumes'), list) or len(data['volumes']) > 16 or
            not isinstance(data.get('hid_nodes'), list) or len(data['hid_nodes']) > 16 or
            not isinstance(data.get('usb_nodes'), list) or len(data['usb_nodes']) > 64):
        raise WinUSBError('Respuesta de volúmenes Windows no válida.')
    data['discovery_stage'] = discovery_stage(data)
    return data


def _chain_parent(chain: list, interface_pattern, expected_service: str) -> str:
    if not isinstance(chain, list) or not 2 <= len(chain) <= 6:
        raise WinUSBError('Falta la cadena PnP del dispositivo compuesto.')
    if any(not isinstance(node, dict) or node.get('status') != 'OK' or
           not isinstance(node.get('instance'), str) for node in chain):
        raise WinUSBError('Un nodo PnP no está operativo.')
    for current, parent in zip(chain, chain[1:]):
        if str(current.get('parent', '')).casefold() != parent['instance'].casefold():
            raise WinUSBError('La cadena de padres PnP no es contigua.')
    interfaces = [node for node in chain if interface_pattern.fullmatch(node['instance'])]
    parents = [node for node in chain if COMPOSITE_PARENT.fullmatch(node['instance'])]
    if (len(interfaces) != 1 or len(parents) != 1 or
            str(interfaces[0].get('service', '')).casefold() != expected_service or
            str(parents[0].get('service', '')).casefold() != 'usbccgp' or
            str(interfaces[0].get('parent', '')).casefold() != parents[0]['instance'].casefold()):
        raise WinUSBError('No coincide la interfaz con el controlador compuesto nativo.')
    hardware_ids = parents[0].get('hardware_ids')
    expected = f'USB\\VID_054C&PID_01CC&REV_{MSC_REVISION:04X}'
    if not isinstance(hardware_ids, list) or not any(
            isinstance(value, str) and value.casefold() == expected.casefold()
            for value in hardware_ids):
        raise WinUSBError('El dispositivo no publica la revisión MSC esperada.')
    return parents[0]['instance']


def correlate_volume(snapshot: dict, hid_path: str) -> dict:
    inventory = snapshot.get('storage_inventory')
    if isinstance(inventory, dict) and inventory.get('errors'):
        raise WinUSBError('La consulta CIM está incompleta; no aceptar una asociación parcial.')
    if not isinstance(hid_path, str) or re.match(
            r'^\\\\\?\\hid#vid_054c&pid_01cc&mi_00(?:&|#)', hid_path, re.I) is None:
        raise WinUSBError('El mando no pertenece al PID exclusivo del diagnostico MSC.')
    volumes = snapshot.get('volumes')
    nodes = snapshot.get('hid_nodes')
    if not isinstance(volumes, list) or len(volumes) != 1 or not isinstance(nodes, list):
        raise WinUSBError('Se requiere un único volumen sintético PSP Dock.')
    volume = volumes[0]
    if (not isinstance(volume, dict) or
            re.fullmatch(r'[A-Za-z]:\\', str(volume.get('root', ''))) is None or
            volume.get('label') != VOLUME_LABEL or
            str(volume.get('serial', '')).replace('-', '').upper() != VOLUME_SERIAL or
            volume.get('filesystem') not in ('FAT', 'FAT12') or
            not MSC_DISK.match(str(volume.get('disk_pnp_id', '')))):
        raise WinUSBError('El volumen no es el FAT12 sintético de la prueba.')
    chain = volume.get('chain')
    parent = _chain_parent(chain, MSC_INTERFACE, 'usbstor')
    if str(chain[0]['instance']).casefold() != volume['disk_pnp_id'].casefold():
        raise WinUSBError('El disco montado no corresponde a la cadena USB.')
    # HID device-interface paths encode the first three PnP components.
    components = hid_path[4:].split('#')
    hid_instance = '\\'.join(components[:3])
    matching = [node for node in nodes if isinstance(node, dict) and
                str(node.get('instance', '')).casefold() == hid_instance.casefold()]
    if len(matching) != 1:
        raise WinUSBError('No se encuentra el nodo PnP exacto del mando HID.')
    hid_parent = _chain_parent(matching[0].get('chain'), HID_INTERFACE, 'hidusb')
    if parent.casefold() != hid_parent.casefold():
        raise WinUSBError('El mando y el disco pertenecen a dispositivos USB distintos.')
    return dict(volume, composite_parent=parent, hid_instance=hid_instance)


class WindowsVolumeFiles:
    """Read normal mounted files using aligned, unbuffered Win32 reads."""

    hardware = True

    def __init__(self):
        if os.name != 'nt':
            raise WinUSBError('La lectura física requiere Windows.')
        self.api = C.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                        W.DWORD, W.DWORD, W.HANDLE]
        self.api.CreateFileW.restype = W.HANDLE
        self.api.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD,
                                     C.POINTER(W.DWORD), C.c_void_p]
        self.api.ReadFile.restype = W.BOOL
        self.api.GetFileSizeEx.argtypes = [W.HANDLE, C.POINTER(C.c_int64)]
        self.api.GetFileSizeEx.restype = W.BOOL
        self.api.CloseHandle.argtypes = [W.HANDLE]
        self.api.CloseHandle.restype = W.BOOL
        self.api.VirtualAlloc.argtypes = [C.c_void_p, C.c_size_t, W.DWORD, W.DWORD]
        self.api.VirtualAlloc.restype = C.c_void_p
        self.api.VirtualFree.argtypes = [C.c_void_p, C.c_size_t, W.DWORD]
        self.api.VirtualFree.restype = W.BOOL

    def chunks(self, path: Path, expected_size: int):
        # Mounted filename only. No raw \\.\PhysicalDrive or volume handles.
        if path.name not in ('PROBE.DAT', 'SESSION.BIN'):
            raise WinUSBError('Solo se leen los dos archivos sintéticos.')
        handle = self.api.CreateFileW(str(path), 0x80000000, 7, None, 3,
                                      0x20000000 | 0x08000000, None)
        if handle in (None, C.c_void_p(-1).value):
            raise WinUSBError(C.get_last_error(), 'No se puede abrir el archivo sin caché.')
        allocation = None
        try:
            size = C.c_int64()
            if not self.api.GetFileSizeEx(handle, C.byref(size)) or size.value != expected_size:
                raise WinUSBError('El tamaño del archivo sintético no coincide.')
            # VirtualAlloc is page-aligned; 64 KiB is also a multiple of
            # the synthetic device's 512-byte logical sector.
            allocation = self.api.VirtualAlloc(None, 65536, 0x3000, 4)
            if not allocation:
                raise WinUSBError(C.get_last_error(), 'No se pudo alinear el buffer USB.')
            total = 0
            while total < expected_size:
                count = W.DWORD()
                if not self.api.ReadFile(handle, allocation, 65536, C.byref(count), None):
                    raise WinUSBError(C.get_last_error(), 'Lectura USB sin caché fallida.')
                if not 0 < count.value <= min(65536, expected_size - total):
                    raise WinUSBError('Lectura USB incompleta o de tamaño inesperado.')
                total += count.value
                yield C.string_at(allocation, count.value)
        finally:
            if allocation:
                self.api.VirtualFree(allocation, 0, 0x8000)
            self.api.CloseHandle(handle)


def _session(files, root: Path) -> bytes:
    value = b''.join(files.chunks(root / 'SESSION.BIN', 16))
    if len(value) != 16 or not any(value):
        raise WinUSBError('SESSION.BIN no contiene una sesión válida.')
    return value


def run_probe(output_directory: Path, *, rounds: int = 4,
              min_duration_seconds: float = 8, discover=discover_volumes,
              hid_probe=None, read_input=None, file_reader=None) -> dict:
    if type(rounds) is not int or not 1 <= rounds <= 32:
        raise ValueError('Se requieren entre 1 y 32 lecturas.')
    if (type(min_duration_seconds) not in (int, float) or
            not math.isfinite(min_duration_seconds) or not 0 <= min_duration_seconds <= 30):
        raise ValueError('Duración mínima entre 0 y 30 segundos.')
    if hid_probe is None:
        from psp_dock.devices.bridge_hid import probe
        hid_probe = lambda: probe(expected_product=MSC_PRODUCT)
    if read_input is None:
        from psp_dock.devices.psp_launcher_input import PSPWinMMReader
        read_input = PSPWinMMReader(expected_product=MSC_PRODUCT).read
    files = file_reader if file_reader is not None else WindowsVolumeFiles()
    output = _new_local_destination(Path(output_directory))
    snapshot = discover()
    volumes = snapshot.get('volumes', [])
    if len(volumes) != 1:
        raise WinUSBError('Debe aparecer un único volumen PSPDOCKTEST.')
    # Correlate PnP before opening any mounted filename. The HID nonce is
    # subsequently compared with bytes read through the storage interface.
    hid_before = hid_probe()
    devices = hid_before.get('devices', []) if isinstance(hid_before, dict) else []
    if len(devices) != 1:
        raise WinUSBError('Se requiere un único mando HID PSP.')
    path = devices[0].get('path', '')
    volume = correlate_volume(snapshot, path)
    volume_root = Path(volume['root'])
    output.mkdir(parents=True, exist_ok=False)
    record = {
        'schema': 'pspdock-msc-hid-acceptance-v1',
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'result': 'not_completed', 'physical_usb_transfer_verified': False,
        'simultaneous_hid_storage_verified': False,
        'synthetic_storage': True, 'real_memory_stick_access': False,
        'psp_writes': False, 'native_usbstor_binding_verified': False,
        'gamepad_motion_observed': False, 'completed_rounds': 0,
        'bytes_verified': 0, 'gamepad_samples_during_transfer': 0,
        'different_gamepad_states': 0, 'minimum_duration_seconds': min_duration_seconds,
        'volume': volume, 'pnp_before': snapshot,
    }
    inputs = set()
    try:
        nonce = _session(files, volume_root)
        before_path = matching_hid_session(hid_before, nonce, expected_product=MSC_PRODUCT)
        _joystick_sample(read_input)
        started = time.monotonic()
        for number in range(rounds):
            target = output / f'probe-{number + 1}.dat'
            partial = output / f'probe-{number + 1}.part'
            digest = hashlib.sha256()
            current = 0
            try:
                with partial.open('xb') as local:
                    for block in files.chunks(volume_root / 'PROBE.DAT', PROBE_SIZE):
                        if not isinstance(block, bytes) or not block or current + len(block) > PROBE_SIZE:
                            raise WinUSBError('Contenido sintético fuera del tamaño previsto.')
                        local.write(block)
                        digest.update(block)
                        current += len(block)
                        deadline = started + min_duration_seconds * (number + current / PROBE_SIZE) / rounds
                        while True:
                            inputs.add(_joystick_sample(read_input))
                            record['gamepad_samples_during_transfer'] += 1
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            time.sleep(min(0.02, remaining))
                    if current != PROBE_SIZE or digest.hexdigest() != PROBE_SHA256:
                        raise WinUSBError('PROBE.DAT no coincide con el SHA-256 independiente.')
                    local.flush()
                    os.fsync(local.fileno())
                if _session(files, volume_root) != nonce:
                    raise WinUSBError('Cambió la sesión del volumen durante la lectura.')
                os.link(partial, target)
            finally:
                partial.unlink(missing_ok=True)
            record['completed_rounds'] += 1
            record['bytes_verified'] += current
        seconds = time.monotonic() - started
        hid_after = hid_probe()
        after_path = matching_hid_session(hid_after, nonce, expected_product=MSC_PRODUCT)
        snapshot_after = discover()
        after_volume = correlate_volume(snapshot_after, after_path)
        if (before_path.casefold() != after_path.casefold() or
                after_volume['composite_parent'].casefold() != volume['composite_parent'].casefold() or
                after_volume['disk_pnp_id'].casefold() != volume['disk_pnp_id'].casefold() or
                after_volume['root'].casefold() != volume['root'].casefold() or
                _session(files, volume_root) != nonce):
            raise WinUSBError('Se sustituyó o reconectó el dispositivo durante la prueba.')
        _joystick_sample(read_input)
        physical = (discover is discover_volumes and os.name == 'nt' and
                    type(files) is WindowsVolumeFiles)
        motion = len(inputs) > 1
        record.update({
            'result': 'verified' if motion else 'motion_not_observed',
            'physical_usb_transfer_verified': physical,
            'simultaneous_hid_storage_verified': physical and motion,
            'native_usbstor_binding_verified': physical,
            'gamepad_motion_observed': motion,
            'same_hid_storage_session': True, 'session': nonce.hex(),
            'file_read_mode': 'win32-unbuffered' if physical else 'test',
            'seconds': round(seconds, 4), 'probe_sha256': PROBE_SHA256,
            'pnp_after': snapshot_after,
        })
    except BaseException as exc:
        record['result'] = 'interrupted'
        record['error'] = (type(exc).__name__ + ': ' + str(exc))[:300]
        raise
    finally:
        record['different_gamepad_states'] = len(inputs)
        with (output / 'evidence.json').open('x', encoding='utf-8') as local:
            json.dump(record, local, ensure_ascii=False, indent=2)
            local.write('\n')
    return record


def _publish_json(destination: Path, value: dict) -> None:
    """Replace only our own snapshot within a newly created evidence folder."""
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(destination)


def pnp_readiness(snapshot: dict) -> dict:
    """Check existing PnP metadata only, without opening a USB interface/file."""
    volumes, hids = snapshot.get('volumes', []), snapshot.get('hid_nodes', [])
    result = {'ready': False, 'ambiguous': False}
    if not isinstance(volumes, list) or not isinstance(hids, list):
        return dict(result, reason='Falta una respuesta PnP completa.')
    if len(volumes) > 1 or len(hids) > 1:
        return dict(result, ambiguous=True, reason='Hay varios discos o mandos candidatos; no elegir uno.')
    if not volumes:
        stage = snapshot.get('discovery_stage') or discovery_stage(snapshot)
        reasons = {
            'waiting_pnp_disk': 'Esperando al dispositivo de disco USBSTOR.',
            'waiting_hid': 'El disco PnP aparece; esperando a HID antes de consultar CIM.',
            'pnp_disk_without_cim_disk': 'El disco PnP aparece, pero CIM todavía no publica su disco.',
            'cim_disk_without_partition': 'CIM publica el disco sin particiones asociadas.',
            'partition_without_logical_disk': 'CIM publica particiones sin unidad lógica/letra asociada.',
            'cim_query_error': 'La consulta CIM falló; se conserva el estadio y el error.',
        }
        return dict(result, discovery_stage=stage,
                    reason=reasons.get(stage, 'Esperando al volumen PSPDOCKTEST.'))
    if not hids:
        return dict(result, reason='El volumen ya aparece; esperando a su interfaz HID.')
    instance = hids[0].get('instance', '') if isinstance(hids[0], dict) else ''
    if not isinstance(instance, str) or len(instance.split('\\')) != 3:
        return dict(result, reason='La identidad PnP HID todavía no está disponible.')
    # This synthetic path is used only to reuse the pure PnP correlation
    # checks. It is NEVER passed to CreateFile, HID APIs or the file reader.
    path = '\\\\?\\' + instance.replace('\\', '#') + '#pnp-preflight'
    try:
        volume = correlate_volume(snapshot, path)
    except WinUSBError as exc:
        return dict(result, reason=str(exc))
    return dict(result, ready=True, composite_parent=volume['composite_parent'])


class TransitionHistory:
    """Append changed states durably; never rewrite an earlier observation."""

    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, path: Path, started: float):
        self.path, self.started = path, started
        self.count = self.bytes = 0
        self.full = False
        self.previous = {}
        with path.open('x', encoding='utf-8'):
            pass

    def append(self, kind: str, state: dict, *, observed_utc: str | None = None,
               completed_utc: str | None = None) -> bool:
        canonical = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        if self.previous.get(kind) == canonical:
            return True
        event = {'sequence': self.count + 1, 'utc': datetime.now(timezone.utc).isoformat(),
                 'elapsed_seconds': round(time.monotonic() - self.started, 4),
                 'kind': kind, 'state': state}
        # Capture times are metadata, outside the deduplicated state.
        if observed_utc is not None:
            event['observed_utc'] = observed_utc
        if completed_utc is not None:
            event['completed_utc'] = completed_utc
        data = (json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        if self.bytes + len(data) > self.MAX_BYTES:
            self.full = True
            return False
        with self.path.open('ab') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        self.count += 1
        self.bytes += len(data)
        self.previous[kind] = canonical
        return True


def _transition_state(observation: dict) -> dict:
    storage = observation.get('storage', {})
    result = {}
    for key, identity in (('usb_nodes', 'instance'), ('hid_nodes', 'instance'), ('volumes', 'root')):
        values = storage.get(key, [])
        result[key] = sorted(values, key=lambda row: str(row.get(identity, ''))
                             if isinstance(row, dict) else str(row)) if isinstance(values, list) else values
    for key in ('storage_inventory', 'uncorrelated_volume_inventory', 'discovery_stage'):
        if key in storage:
            result[key] = storage[key]
    for key in ('readiness', 'discovery_error'):
        if key in observation:
            result[key] = observation[key]
    return result


def monitor(output_directory: Path, *, wait_seconds: int = 0,
            rounds: int = 8, min_duration_seconds: float = 8,
            discover=discover_volumes, diagnostics=None, acceptance=run_probe) -> dict:
    """Persist enumeration failures too; execute at most one physical probe.

    output_directory is always a NEW fixed-disk directory. Snapshots and
    monitor.json live at its root; the acceptance result and copied files
    live under probe/. A missing drive letter still leaves diagnostic data.
    """
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 1800:
        raise ValueError('La espera debe estar entre 0 y 1800 segundos.')
    if type(rounds) is not int or not 1 <= rounds <= 32:
        raise ValueError('Se requieren entre 1 y 32 lecturas.')
    if (type(min_duration_seconds) not in (int, float) or
            not math.isfinite(min_duration_seconds) or not 0 <= min_duration_seconds <= 30):
        raise ValueError('Duración mínima entre 0 y 30 segundos.')
    if diagnostics is None:
        from psp_dock.devices.psp_composite_diagnostics import inspect
        diagnostics = inspect
    root = _new_local_destination(Path(output_directory))
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + wait_seconds
    last_global_scan = None
    history = TransitionHistory(root / 'transitions.jsonl', started)
    record = {
        'schema': 'pspdock-msc-monitor-v1',
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'result': 'waiting', 'probe_attempts': 0,
        'physical_usb_transfer_verified': False,
        'simultaneous_hid_storage_verified': False,
        'synthetic_storage': True, 'real_memory_stick_access': False,
        'psp_writes': False, 'driver_changes': False,
        'wait_seconds': wait_seconds, 'observations': 0,
        'probe_directory': str(root / 'probe'),
        'transition_history': str(history.path), 'transition_history_max_bytes': history.MAX_BYTES,
        'global_volume_scan_interval_seconds': 15,
    }

    def capture():
        result = {'captured_utc': datetime.now(timezone.utc).isoformat()}
        try:
            result['diagnostics'] = diagnostics()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result['diagnostic_error'] = (type(exc).__name__ + ': ' + str(exc))[:300]
        data = result.get('diagnostics', {})
        # SetupAPI sections stay in snapshots. Transition history retains
        # device identities/status/errors, not repeated driver-install logs.
        devices = data.get('devices', []) if isinstance(data, dict) else []
        state = {'devices': sorted(devices, key=lambda row: str(row.get('instance', ''))
                                   if isinstance(row, dict) else str(row))}
        if 'diagnostic_error' in result:
            state['diagnostic_error'] = result['diagnostic_error']
        history.append('diagnostics', state)
        return result

    _publish_json(root / 'monitor.json', record)
    try:
        history.append('monitor', {'result': 'waiting', 'revision': f'{MSC_REVISION:04X}', 'probe_attempts': 0})
        latest = capture()
        _publish_json(root / 'before.json', latest)
        _publish_json(root / 'latest.json', latest)
        last_diagnostics = time.monotonic()
        while True:
            record['observations'] += 1
            observation = {'observed_utc': datetime.now(timezone.utc).isoformat()}
            try:
                if discover is discover_volumes:
                    scan_due = last_global_scan is None or time.monotonic() - last_global_scan >= 15
                    snapshot = discover(include_global_volumes=scan_due)
                    global_scan = snapshot.get('uncorrelated_volume_inventory', {})
                    if isinstance(global_scan, dict) and global_scan.get('queried') is True:
                        last_global_scan = time.monotonic()
                else:
                    snapshot = discover()
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get('volumes'), list):
                    raise WinUSBError('Respuesta de descubrimiento MSC no válida.')
                observation['storage'] = snapshot
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                snapshot = {'volumes': []}
                observation['discovery_error'] = (type(exc).__name__ + ': ' + str(exc))[:300]
            observation['completed_utc'] = datetime.now(timezone.utc).isoformat()
            readiness = pnp_readiness(snapshot)
            observation['readiness'] = readiness
            record['last_readiness'] = readiness
            if not history.append('observation', _transition_state(observation),
                                  observed_utc=observation['observed_utc'],
                                  completed_utc=observation['completed_utc']):
                raise WinUSBError('El historial alcanzó 8 MiB; se detiene la observación sin descartar estados anteriores.')
            if readiness['ready'] or readiness['ambiguous'] or time.monotonic() - last_diagnostics >= 15:
                latest = capture()
                last_diagnostics = time.monotonic()
            latest['observation'] = observation
            _publish_json(root / 'latest.json', latest)
            _publish_json(root / 'monitor.json', record)
            if readiness['ambiguous']:
                raise WinUSBError(readiness['reason'])
            if readiness['ready']:
                record['result'] = 'probing'
                record['probe_attempts'] = 1
                _publish_json(root / 'monitor.json', record)
                history.append('monitor', {'result': 'probing', 'probe_attempts': 1})
                probe = acceptance(root / 'probe', rounds=rounds,
                                   min_duration_seconds=min_duration_seconds)
                record['probe'] = probe
                record['result'] = probe['result']
                record['physical_usb_transfer_verified'] = probe.get('physical_usb_transfer_verified') is True
                record['simultaneous_hid_storage_verified'] = probe.get('simultaneous_hid_storage_verified') is True
                return record
            if time.monotonic() >= deadline:
                record['result'] = 'timed_out'
                raise WinUSBError('No se completó la enumeración conjunta USBSTOR + HID; se conservaron los diagnósticos.')
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    except BaseException as exc:
        if record['result'] != 'timed_out':
            record['result'] = 'interrupted'
        record['error'] = (type(exc).__name__ + ': ' + str(exc))[:300]
        raise
    finally:
        record['finished_utc'] = datetime.now(timezone.utc).isoformat()
        record['seconds'] = round(time.monotonic() - started, 4)
        _publish_json(root / 'after.json', capture())
        history.append('monitor', {'result': record['result'], 'probe_attempts': record['probe_attempts'],
                                  'error': record.get('error'),
                                  'physical_usb_transfer_verified': record['physical_usb_transfer_verified']})
        record['transition_count'] = history.count
        record['transition_history_limit_reached'] = history.full
        _publish_json(root / 'monitor.json', record)


def main() -> int:
    parser = argparse.ArgumentParser(description='Prueba física de almacenamiento sintético USBSTOR + HID')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--rounds', type=int, default=8)
    parser.add_argument('--min-duration-seconds', type=float, default=8)
    parser.add_argument('--wait-seconds', type=int, default=0)
    args = parser.parse_args()
    try:
        if not args.run:
            print(json.dumps(discover_volumes(), ensure_ascii=False, indent=2))
            return 0
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('La prueba física requiere Windows fuera de los tests.')
        if args.output is None or not 0 <= args.wait_seconds <= 1800:
            raise ValueError('Indica una carpeta nueva del PC y una espera de 0 a 1800 segundos.')
        result = monitor(args.output, wait_seconds=args.wait_seconds,
                         rounds=args.rounds, min_duration_seconds=args.min_duration_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['result'] == 'verified' else 2
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'physical_usb_transfer_verified': False,
                          'psp_writes': False}, ensure_ascii=False, indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
