"""One combined, explicitly invoked read-only acceptance test for PDC2.

Verifies the HID and WinUSB interfaces publish the SAME live session,
then reads only the synthetic bundled PROBE.DAT repeatedly. No write
protocol exists. Original games, SAVEDATA, Windows drivers and PSP
flash are never modified. A successful simulator cannot claim a physical
USB result; the returned evidence states its backend explicitly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time

from psp_dock.devices.psp_composite_client import CompositeReader, PROBE_PATH, PROBE_SHA256, PROBE_SIZE
from psp_dock.devices.psp_winusb_probe import candidate_paths
from psp_dock.devices.psp_winusb_transport import WinUSBTransport, WinUSBError


HID_INTERFACE = re.compile(r"^\\\\\?\\hid#vid_054c&pid_01cb&mi_00(?:&|#)", re.I)


def matching_hid_session(report: dict, session: bytes, *, expected_product: int = 0x01CB) -> str:
    """Correlate two interfaces; this is NOT cryptographic authentication."""
    if type(expected_product) is not int or expected_product not in (0x01CB, 0x01CC):
        raise WinUSBError('PID PSP Dock no reconocido.')
    expected_interface = HID_INTERFACE if expected_product == 0x01CB else re.compile(
        r'^\\\\\?\\hid#vid_054c&pid_01cc&mi_00(?:&|#)', re.I)
    if (not isinstance(report, dict) or report.get('supported') is not True or
            report.get('errors') or not isinstance(report.get('devices'), list) or
            len(report['devices']) != 1 or len(session) != 16 or not any(session)):
        raise WinUSBError('No hay un único mando HID PSP verificable.')
    device = report['devices'][0]
    if not isinstance(device, dict) or not expected_interface.match(device.get('path', '')):
        raise WinUSBError('El mando no es la interfaz HID del dispositivo compuesto.')
    status = device.get('status')
    if (not isinstance(status, dict) or status.get('session') != session.hex() or
            status.get('connected') is not True or status.get('version') != 1 or
            status.get('mode') != 2 or status.get('pending') is not False or
            status.get('consumed') is not False or status.get('sequence') != 0 or
            status.get('result') != 0):
        raise WinUSBError('HID y BULK no corresponden a la misma sesión PSP limpia.')
    return device['path']


def _joystick_sample(read_input) -> tuple:
    frame = read_input()
    if (not isinstance(frame, dict) or frame.get('connected') is not True or
            frame.get('source') != 'psp_winmm' or
            type(frame.get('button_mask')) is not int):
        raise WinUSBError('El gamepad dejó de responder durante la transferencia.')
    return (frame['button_mask'], *(bool(frame.get(k)) for k in
                                   ('left', 'right', 'up', 'down')))


def run_probe(output_directory: Path, *, rounds: int = 4,
              min_duration_seconds: float = 0,
              enumerate_bulk=candidate_paths, open_transport=WinUSBTransport,
              hid_probe=None, read_input=None, storage_present=None) -> dict:
    """Bounded test length is NOT a watchdog on the PSP's gamepad session."""
    if type(rounds) is not int or not 1 <= rounds <= 32:
        raise ValueError('La prueba admite de 1 a 32 lecturas del archivo ficticio.')
    if (type(min_duration_seconds) not in (int, float) or
            not math.isfinite(min_duration_seconds) or
            not 0 <= min_duration_seconds <= 30):
        raise ValueError('La duración mínima debe estar entre 0 y 30 segundos.')
    if hid_probe is None:
        from psp_dock.devices.bridge_hid import probe
        hid_probe = probe
    if read_input is None:
        from psp_dock.devices.psp_launcher_input import PSPWinMMReader
        read_input = PSPWinMMReader().read
    if storage_present is None:
        from dock import windows_devices
        storage_present = lambda: bool(windows_devices())
    # Validate destination before sending even HELLO. No overwrite and no
    # use of a removable PSP volume as a supposed PC test directory.
    from psp_dock.devices.psp_composite_client import _new_local_destination
    root = _new_local_destination(Path(output_directory))
    paths = enumerate_bulk()
    if len(paths) != 1 or storage_present():
        raise WinUSBError('Se requiere una interfaz BULK única y ninguna PSP en USBSTOR.')
    root.mkdir(parents=True, exist_ok=False)
    report = {
        'schema': 'pspdock-composite-acceptance-v1',
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'result': 'not_completed', 'physical_usb_transfer_verified': False,
        'same_hid_bulk_session': False, 'psp_writes': False,
        'save_return_supported': False, 'fat_volume_identity_verified': False,
        'gamepad_motion_observed': False, 'completed_rounds': 0,
        'bytes_verified': 0, 'gamepad_samples_during_transfer': 0,
        'minimum_duration_seconds': min_duration_seconds,
        'simultaneous_hid_bulk_verified': False,
    }
    inputs = set()
    try:
        with open_transport(paths[0]) as transport:
            before = matching_hid_session(hid_probe(), transport.firmware.session)
            _joystick_sample(read_input)
            reader = CompositeReader(transport)
            listed = reader.list_directory('PSP/GAME/PSPDockCompositeTest')
            if not any(x['name'] == 'PROBE.DAT' and x['size'] == PROBE_SIZE and
                       x['directory'] is False for x in listed):
                raise WinUSBError('Falta el archivo de prueba publicado por la PSP.')
            started = time.monotonic()
            for number in range(rounds):
                def progress(current, total):
                    # Spread actual file reads over the observation interval.
                    # The PSP still owns the file and another READ/EOF follows
                    # this callback. Do not append a joystick-only waiting period
                    # after BULK is finished and call it simultaneous input.
                    deadline = started + min_duration_seconds * (number + current / total) / rounds
                    while True:
                        inputs.add(_joystick_sample(read_input))
                        report['gamepad_samples_during_transfer'] += 1
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        time.sleep(min(0.02, remaining))
                result = reader.copy_to_pc(
                    PROBE_PATH, root / ('probe-' + str(number + 1) + '.dat'),
                    expected_sha256=PROBE_SHA256, expected_size=PROBE_SIZE,
                    progress=progress,
                )
                report['completed_rounds'] += 1
                report['bytes_verified'] += result['bytes']
                transport.check_session()
            after = matching_hid_session(hid_probe(), transport.firmware.session)
            if before.casefold() != after.casefold() or storage_present():
                raise WinUSBError('Cambió el mando o reapareció almacenamiento durante BULK.')
            _joystick_sample(read_input)
            motion = len(inputs) > 1
            hardware = getattr(transport.backend, 'hardware', False) is True
            report.update({
                'result': 'verified' if motion else 'motion_not_observed',
                'usb_backend': 'winusb' if hardware else 'test',
                'physical_usb_transfer_verified': hardware,
                'same_hid_bulk_session': True,
                'gamepad_motion_observed': motion,
                'simultaneous_hid_bulk_verified': hardware and motion,
                'different_gamepad_states': len(inputs),
                'seconds': round(time.monotonic() - started, 4),
                'probe_sha256': PROBE_SHA256,
            })
    except BaseException as exc:
        report['result'] = 'interrupted'
        report['error'] = (type(exc).__name__ + ': ' + str(exc))[:300]
        raise
    finally:
        # Failed evidence is retained too. Do not infer success from the
        # existence of a report file or a partially completed round.
        report['different_gamepad_states'] = len(inputs)
        with (root / 'evidence.json').open('x', encoding='utf-8') as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
            file.write('\n')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description='Ensayo conjunto HID+BULK, solo lectura')
    parser.add_argument('--run', action='store_true', help='Ejecutar prueba explícita; sin esto solo enumera')
    parser.add_argument('--output', type=Path, help='Carpeta NUEVA en un disco fijo del PC')
    parser.add_argument('--rounds', type=int, default=4)
    parser.add_argument('--min-duration-seconds', type=float, default=8,
                        help='Distribuir las lecturas durante este intervalo para comprobar botones')
    args = parser.parse_args()
    try:
        if not args.run:
            print(json.dumps({'candidate_interfaces': candidate_paths(),
                              'psp_transactions_sent': False,
                              'physical_usb_transfer_verified': False}, indent=2))
            return 0
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('El ensayo físico solo se ejecuta en Windows fuera de los tests.')
        if args.output is None:
            raise ValueError('Indica --output con una carpeta nueva del PC.')
        result = run_probe(args.output, rounds=args.rounds,
                           min_duration_seconds=args.min_duration_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['result'] == 'verified' else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print('Prueba no completada:', str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
