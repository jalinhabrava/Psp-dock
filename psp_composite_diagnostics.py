"""Read Windows PnP binding and optional PDC2 descriptor details.

This is not a driver installer. Never calls Zadig, PnP rebind/reset,
registry mutation, USBSTOR eject or filesystem commands. An absent
WinUSB interface is a diagnostic result, not a reason to change drivers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from psp_winusb_probe import candidate_paths, INTERFACE_GUID


PNP_SCRIPT = r'''
$ErrorActionPreference='Stop'
$keys=@('DEVPKEY_Device_HardwareIds','DEVPKEY_Device_CompatibleIds',
        'DEVPKEY_Device_Service','DEVPKEY_Device_Parent',
        'DEVPKEY_Device_DriverInfPath','DEVPKEY_Device_ProblemCode',
        'DEVPKEY_Device_LocationInfo','DEVPKEY_Device_LocationPaths',
        'DEVPKEY_Device_ContainerId','DEVPKEY_Device_BusReportedDeviceDesc')
$result=@(Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match '^(USB|HID)\\VID_054C&PID_(01CB|01CC|02D2)' -or
    $_.InstanceId -match '^USBSTOR\\DISK&VEN_SONY&PROD_.*PSP' -or
    $_.InstanceId -match '^USBSTOR\\DISK&VEN_PSPDOCK&PROD_MSC_HID_TEST' -or
    $_.InstanceId -match '^USB\\(VID_0000&PID_000[02]|UNKNOWN)'
} | ForEach-Object {
    $device=$_
    $props=@{}
    $properties=@(Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName $keys -ErrorAction SilentlyContinue)
    foreach($prop in $properties) {
        if($null -ne $prop) {$props[$prop.KeyName]=$prop.Data}
    }
    $unidentified=$device.InstanceId -match '^USB\\(VID_0000&PID_000[02]|UNKNOWN)'
    [PSCustomObject]@{instance=$device.InstanceId;class=$device.Class;
        status=$device.Status;name=$device.FriendlyName;properties=$props;
        unidentified_usb_failure=$unidentified;psp_identity_confirmed=(-not $unidentified)}
})
ConvertTo-Json -InputObject $result -Depth 6 -Compress
'''


def windows_binding_snapshot(*, run=subprocess.run) -> dict:
    if os.name != 'nt':
        return {'supported': False, 'devices': [], 'driver_changes': False}
    result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', PNP_SCRIPT],
                 capture_output=True, text=True, timeout=40, creationflags=0x08000000)
    if result.returncode:
        raise OSError('Consulta Windows PnP falló: ' + result.stderr[:180])
    if len(result.stdout) > 128 * 1024:
        raise ValueError('Windows devolvió demasiados datos PnP.')
    rows = json.loads(result.stdout.lstrip('\ufeff'))
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError('Respuesta PnP no reconocida.')
    services = [str(row.get('properties', {}).get('DEVPKEY_Device_Service', '')).casefold()
                for row in rows if isinstance(row, dict)]
    return {'supported': True, 'devices': rows, 'driver_changes': False,
            'usbccgp_present': 'usbccgp' in services,
            'winusb_present': 'winusb' in services,
            'hidusb_present': 'hidusb' in services}


SETUPAPI_RELEVANT = re.compile(r'VID_054C&PID_(?:01CB|01CC|02D2)|VID_0000&PID_000[02]', re.I)


def setupapi_snapshot(log_path: Path | None = None) -> dict:
    """Keep only recent PSP/failed-enumeration blocks, never the full log.

    Failed USB descriptor requests have no Sony VID. Their location and time
    must be compared with the before/after PnP snapshots; they are not proof
    that the failed device is the PSP.
    """
    if log_path is None:
        if os.name != 'nt':
            return {'supported': False, 'sections': [], 'driver_changes': False}
        log_path = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'INF' / 'setupapi.dev.log'
    report = {'supported': True, 'sections': [], 'driver_changes': False,
              'maximum_tail_bytes': 2 * 1024 * 1024,
              'unidentified_usb_entries_are_not_psp_proof': True}
    try:
        with Path(log_path).open('rb') as source:
            size = source.seek(0, os.SEEK_END)
            offset = max(0, size - report['maximum_tail_bytes'])
            # Preserve UTF-16 code unit alignment if the system uses it.
            source.seek(offset - offset % 2)
            data = source.read(report['maximum_tail_bytes'] + 2)
        report['tail_truncated'] = offset > 0
        encoding = 'utf-16-le' if data.count(b'\0') > len(data) // 8 else 'utf-8'
        text = data.decode(encoding, errors='replace').lstrip('\ufeff')
        sections = re.split(r'(?=^>>>\s+\[)', text, flags=re.M)
        selected = [part for part in sections if SETUPAPI_RELEVANT.search(part)][-8:]
        report['sections'] = [part if len(part) <= 16000 else
                              part[:500] + '\n[section truncated]\n' + part[-15000:]
                              for part in selected]
    except OSError as exc:
        report['error'] = (type(exc).__name__ + ': ' + str(exc))[:240]
    return report


def inspect(*, firmware: bool = False) -> dict:
    report = windows_binding_snapshot()
    report['setupapi'] = setupapi_snapshot()
    report['bulk_interface_guid'] = str(INTERFACE_GUID)
    paths = candidate_paths()
    report['bulk_candidates'] = paths
    report['physical_transfer_verified'] = False
    report['file_commands_sent'] = False
    if firmware:
        if len(paths) != 1:
            report['firmware_error'] = 'Falta una única interfaz BULK enumerada; no abrir ningún dispositivo.'
        else:
            from psp_winusb_transport import WinUSBTransport
            try:
                with WinUSBTransport(paths[0]) as link:
                    report['usb_descriptors'] = link.descriptor
                    report['firmware_status'] = {
                        'active': link.firmware.active, 'connected': link.firmware.connected,
                        'commands': link.firmware.commands, 'session_present': any(link.firmware.session),
                    }
            except (OSError, ValueError) as exc:
                report['firmware_error'] = str(exc)
    return report


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description='Diagnóstico PSP compuesto de solo lectura')
    cli.add_argument('--firmware', action='store_true', help='Leer descriptor y estado PDC2, sin comandos de archivo')
    args = cli.parse_args()
    try:
        print(json.dumps(inspect(firmware=args.firmware), ensure_ascii=False, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'driver_changes': False}))
        raise SystemExit(1)
