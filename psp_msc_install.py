"""Install the synthetic MSC + HID diagnostic in a new PSP game folder."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import uuid

from psp_composite_install import no_links, matching_backup
from psp_msc_package import FILES, REMOTE_FOLDER, digest, verify_package
from psp_usb import identify, UsbTransitionError


def plan_install(package: Path, root: Path, snapshot: Path,
                 serial: str, usb_id: str) -> dict:
    package, root, snapshot = map(Path, (package, root, snapshot))
    for path in (package, root, snapshot, root / 'PSP/GAME'):
        no_links(path)
    manifest = verify_package(package)
    if not (root / 'PSP/GAME').is_dir():
        raise ValueError('No existe PSP/GAME en el dispositivo identificado.')
    target = root / REMOTE_FOLDER
    if target.exists():
        raise FileExistsError('Ya existe PSPDockStorageTest; no se sobrescribe.')
    matching_backup(root, snapshot, serial, usb_id)
    size = sum(item['size'] for item in manifest['files'].values())
    if shutil.disk_usage(root).free < size + 8 * 1024 * 1024:
        raise ValueError('Falta espacio para la prueba en la Memory Stick.')
    return {'target': str(target), 'backup': str(snapshot), 'revision': manifest['revision'],
            'files': manifest['files'], 'xmb': manifest['xmb'], 'bytes': size,
            'synthetic_storage': True, 'real_memory_stick_access': False,
            'installed': False, 'hardware_execution_verified': False, 'autorun': False}


def install_package(package: Path, root: Path, snapshot: Path,
                    serial: str, usb_id: str, *, same_device) -> dict:
    if not same_device():
        raise ValueError('Cambio la identidad USB antes de preparar la prueba.')
    plan = plan_install(package, root, snapshot, serial, usb_id)
    target = Path(plan['target'])
    staging = target.parent / ('.pspdock-msc-' + uuid.uuid4().hex)
    staging.mkdir(exist_ok=False)
    # Keep our partial directory on failure; never delete from a possibly
    # different volume after a disconnect or drive-letter reassignment.
    for name in FILES:
        source, copied = Path(package) / REMOTE_FOLDER / name, staging / name
        expected = plan['files'][name]
        with source.open('rb') as reader, copied.open('xb') as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if copied.stat().st_size != expected['size'] or digest(copied) != expected['sha256']:
            raise ValueError('La copia PSP no coincide con el paquete: ' + name)
    verify_package(package)
    if not same_device():
        raise ValueError('Cambio la identidad USB durante la copia; no se publica.')
    matching_backup(Path(root), Path(snapshot), serial, usb_id)
    if target.exists():
        raise FileExistsError('Aparecio PSPDockStorageTest; no se sobrescribe.')
    staging.rename(target)
    if not same_device():
        raise ValueError('La PSP se desconecto al publicar; verificar antes de ejecutar.')
    for name, record in plan['files'].items():
        if digest(target / name) != record['sha256']:
            raise ValueError('Fallo la relectura final de la prueba: ' + name)
    return {**plan, 'installed': True, 'filesystem_writes_only': REMOTE_FOLDER}


def main() -> int:
    parser = argparse.ArgumentParser(description='Instalar Storage + HID con thumbnail y fondo XMB')
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--drive', required=True)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--receipt', type=Path, help='Archivo NUEVO de evidencia en el PC')
    args = parser.parse_args()
    try:
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('El instalador fisico requiere Windows fuera de las pruebas.')
        identity, _ = identify(args.drive)
        root = Path(identity.drive)
        receipt = None
        if args.receipt:
            from psp_composite_client import _new_local_destination
            receipt = _new_local_destination(args.receipt)
        if args.install:
            result = install_package(args.package, root, args.backup,
                identity.volume_serial, identity.usb_device_id,
                same_device=lambda: identify(identity.drive)[0] == identity)
        else:
            result = plan_install(args.package, root, args.backup,
                identity.volume_serial, identity.usb_device_id)
        result['identity'] = asdict(identity)
        if receipt:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            with receipt.open('x', encoding='utf-8') as writer:
                json.dump(result, writer, ensure_ascii=False, indent=2)
                writer.write('\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, UsbTransitionError) as exc:
        print('Prueba no instalada/verificada:', str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
