"""Stage the reviewed composite test in a new folder on a backed-up PSP.

This copies files; it never starts firmware, ejects USB, edits plugins or
rebinds a Windows driver. Execution on the PSP is a separate physical action.
The normal PSP/GAME/PSPDock application and all existing data stay intact.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import uuid

from psp_backup import selection, verify_archive
from psp_composite_package import FILES, REMOTE_FOLDER, digest, verify_package
from psp_usb import identify, UsbTransitionError


def no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('No se admiten enlaces en la ruta del ensayo.')


def matching_backup(root: Path, snapshot: Path, serial: str, usb_id: str) -> dict:
    verify_archive(snapshot)
    saved = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    if (saved.get('volume_serial') != serial or saved.get('usb_device_id') != usb_id or
            saved.get('scope') not in ('critical', 'full')):
        raise ValueError('El backup no corresponde a esta PSP y Memory Stick.')
    current, _ = selection(root, 'critical')
    if not current:
        raise ValueError('No se ha verificado el estado actual de la PSP.')
    if saved['scope'] == 'critical' and set(saved['files']) != {p for p, _ in current}:
        raise ValueError('El contenido crítico cambió desde el backup.')
    for relative, size in current:
        record = saved['files'].get(relative)
        if not record or record['size'] != size or digest(root / relative) != record['sha256']:
            raise ValueError('El backup no refleja el estado actual: ' + relative)
    return saved


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
        raise FileExistsError('Ya existe PSPDockCompositeTest; no se sobrescribe.')
    matching_backup(root, snapshot, serial, usb_id)
    size = sum(item['size'] for item in manifest['files'].values())
    if shutil.disk_usage(root).free < size + 8 * 1024 * 1024:
        raise ValueError('Falta espacio para el ensayo en la Memory Stick.')
    return {'target': str(target), 'backup': str(snapshot), 'revision': manifest['revision'],
            'files': manifest['files'], 'bytes': size, 'installed': False,
            'hardware_execution_verified': False, 'autorun': False}


def install_package(package: Path, root: Path, snapshot: Path,
                    serial: str, usb_id: str, *, same_device) -> dict:
    if not same_device():
        raise ValueError('Cambió la identidad USB antes de preparar el ensayo.')
    plan = plan_install(package, root, snapshot, serial, usb_id)
    target = Path(plan['target'])
    staging = target.parent / ('.pspdock-composite-' + uuid.uuid4().hex)
    staging.mkdir(exist_ok=False)
    # On failure keep our uniquely named partial folder. Never delete from
    # a mount whose identity may have changed after a cable interruption.
    for name in FILES:
        source, copied = Path(package) / REMOTE_FOLDER / name, staging / name
        expected = plan['files'][name]
        with source.open('rb') as inp, copied.open('xb') as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        if copied.stat().st_size != expected['size'] or digest(copied) != expected['sha256']:
            raise ValueError('La copia PSP no coincide con el paquete: ' + name)
    verify_package(package)
    if not same_device():
        raise ValueError('Cambió la identidad USB durante la copia; no se publica.')
    matching_backup(Path(root), Path(snapshot), serial, usb_id)
    if target.exists():
        raise FileExistsError('Apareció un destino PSPDockCompositeTest; no se sobrescribe.')
    # Windows rename rejects an existing destination. Both paths are on the
    # same PSP volume; no existing application can be replaced by this move.
    staging.rename(target)
    if not same_device():
        raise ValueError('La PSP se desconectó tras publicar; verificar antes de ejecutar.')
    for name, record in plan['files'].items():
        if digest(target / name) != record['sha256']:
            raise ValueError('Falló la relectura final del ensayo: ' + name)
    return {**plan, 'installed': True, 'filesystem_writes_only': REMOTE_FOLDER}


def main() -> int:
    parser = argparse.ArgumentParser(description='Copiar el ensayo USB compuesto a una carpeta NUEVA')
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--drive', required=True)
    parser.add_argument('--install', action='store_true', help='Realizar la copia verificada')
    parser.add_argument('--receipt', type=Path, help='Archivo NUEVO de evidencia en el PC')
    args = parser.parse_args()
    try:
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('El instalador físico requiere Windows fuera de las pruebas.')
        identity, _ = identify(args.drive)
        root = Path(identity.drive)
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
        if args.receipt:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            with receipt.open('x', encoding='utf-8') as out:
                json.dump(result, out, ensure_ascii=False, indent=2)
                out.write('\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, UsbTransitionError) as exc:
        print('Ensayo no instalado/verificado:', str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
