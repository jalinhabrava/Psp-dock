"""Read Windows PnP binding and optional PDC2 descriptor details.

This is not a driver installer. Never calls Zadig, PnP rebind/reset,
registry mutation, USBSTOR eject or filesystem commands. An absent
WinUSB interface is a diagnostic result, not a reason to change drivers.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

from psp_winusb_probe import candidate_paths, INTERFACE_GUID


PNP_SCRIPT = r'''
$ErrorActionPreference='Stop'
$keys=@('DEVPKEY_Device_HardwareIds','DEVPKEY_Device_CompatibleIds',
        'DEVPKEY_Device_Service','DEVPKEY_Device_Parent',
        'DEVPKEY_Device_DriverInfPath','DEVPKEY_Device_ProblemCode')
$result=@(Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match '^(USB|HID)\\VID_054C&PID_(01CB|02D2)' -or
    $_.InstanceId -match '^USBSTOR\\DISK&VEN_SONY&PROD_.*PSP'
} | ForEach-Object {
    $device=$_
    $props=@{}
    foreach($key in $keys) {
        $prop=Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName $key -ErrorAction SilentlyContinue
        if($null -ne $prop) {$props[$key]=$prop.Data}
    }
    [PSCustomObject]@{instance=$device.InstanceId;class=$device.Class;
        status=$device.Status;name=$device.FriendlyName;properties=$props}
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
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError('Respuesta PnP no reconocida.')
    services = [str(row.get('properties', {}).get('DEVPKEY_Device_Service', '')).casefold()
                for row in rows if isinstance(row, dict)]
    return {'supported': True, 'devices': rows, 'driver_changes': False,
            'usbccgp_present': 'usbccgp' in services,
            'winusb_present': 'winusb' in services,
            'hidusb_present': 'hidusb' in services}


def inspect(*, firmware: bool = False) -> dict:
    report = windows_binding_snapshot()
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
