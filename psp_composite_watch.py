"""Wait for one physical PSP composite test and preserve its evidence.

Runs only when explicitly requested. It queries Windows while waiting,
then starts ONE read-only synthetic-file test. Never installs drivers,
retries a failed transfer, writes PSP files or changes the stable Dock.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from psp_composite_acceptance import run_probe
from psp_composite_client import _new_local_destination
from psp_composite_diagnostics import inspect
from psp_winusb_probe import candidate_paths


def write_evidence(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def watch(output: Path, *, wait_seconds: int = 1800, rounds: int = 32) -> dict:
    if not 1 <= wait_seconds <= 7200 or not 1 <= rounds <= 32:
        raise ValueError('Tiempo o número de lecturas fuera de límites.')
    root = _new_local_destination(output)
    root.mkdir(parents=True, exist_ok=False)
    state = {'schema': 'pspdock-composite-watch-v1', 'status': 'waiting',
             'started_utc': datetime.now(timezone.utc).isoformat(),
             'physical_usb_transfer_verified': False, 'psp_file_writes': False,
             'driver_changes': False, 'attempts': 0}
    write_evidence(root / 'watch.json', state)
    try:
        write_evidence(root / 'before-pnp.json', inspect())
        deadline = time.monotonic() + wait_seconds
        next_snapshot = time.monotonic() + 15
        while time.monotonic() < deadline:
            paths = candidate_paths()
            if len(paths) > 1:
                raise ValueError('Más de una interfaz PSP candidata; no se inicia el ensayo.')
            if paths:
                # Let Windows finish binding HID after creating the WinUSB
                # interface. This is not a file-transfer retry.
                time.sleep(2)
                state['status'] = 'testing'
                state['attempts'] = 1
                write_evidence(root / 'watch.json', state)
                write_evidence(root / 'enumerated-pnp.json', inspect())
                result = run_probe(root / 'transfer', rounds=rounds, min_duration_seconds=8)
                state.update({'status': result['result'],
                    'physical_usb_transfer_verified': result['physical_usb_transfer_verified'],
                    'gamepad_motion_observed': result['gamepad_motion_observed'],
                    'same_hid_bulk_session': result['same_hid_bulk_session'],
                    'bytes_verified': result['bytes_verified']})
                break
            # This slower snapshot also records Code 28 / missing WinUSB,
            # which would never become a candidate interface GUID.
            if time.monotonic() >= next_snapshot:
                write_evidence(root / 'latest-pnp.json', inspect())
                next_snapshot = time.monotonic() + 15
            time.sleep(1)
        else:
            state['status'] = 'timed_out_waiting_for_winusb'
    except BaseException as exc:
        state['status'] = 'interrupted'
        state['error'] = (type(exc).__name__ + ': ' + str(exc))[:400]
        raise
    finally:
        state['updated_utc'] = datetime.now(timezone.utc).isoformat()
        write_evidence(root / 'watch.json', state)
        try:
            write_evidence(root / 'after-pnp.json', inspect())
        except (OSError, ValueError, RuntimeError) as exc:
            write_evidence(root / 'after-pnp-error.json', {'error': str(exc)[:400]})
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description='Esperar una PSP compuesta y medir HID+archivos una vez')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--rounds', type=int, default=32)
    args = parser.parse_args()
    if not args.wait:
        print(json.dumps({'bulk_candidates': candidate_paths(), 'test_started': False}))
        return 0
    try:
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('El ensayo físico requiere Windows fuera de las pruebas.')
        if args.output is None:
            raise ValueError('Indica --output con una carpeta NUEVA del PC.')
        print('Esperando PSP Dock Composite 0204; solo una prueba, sin reintentos.', flush=True)
        result = watch(args.output, wait_seconds=args.seconds, rounds=args.rounds)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if result['status'] == 'verified' else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print('Ensayo detenido: ' + str(exc), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
