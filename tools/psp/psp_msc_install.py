"""Install or replace a receipt-verified synthetic MSC + HID diagnostic."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from tools.psp.psp_composite_install import no_links, matching_backup
from tools.psp.psp_msc_package import FILES, REMOTE_FOLDER, digest, verify_package
from psp_dock.devices.psp_usb import identify, UsbTransitionError


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


def _verify_tree(directory: Path, records: dict) -> None:
    no_links(directory)
    if not directory.is_dir() or {p.name for p in directory.iterdir()} != set(FILES):
        raise ValueError('La carpeta de prueba no contiene exactamente los cuatro archivos revisados.')
    for name in FILES:
        path = directory / name
        no_links(path)
        expected = records[name]
        if (not path.is_file() or path.stat().st_size != expected['size'] or
                digest(path) != expected['sha256']):
            raise ValueError('Archivo distinto del recibo verificado: ' + str(path))


def _copy_records(source: Path, target: Path, records: dict) -> None:
    target.mkdir(exist_ok=False)
    for name in FILES:
        no_links(source / name)
        with (source / name).open('rb') as reader, (target / name).open('xb') as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
    _verify_tree(target, records)


def plan_upgrade(package: Path, root: Path, snapshot: Path,
                 serial: str, usb_id: str, previous_receipt: Path, archive: Path) -> dict:
    package, root, snapshot, previous_receipt, archive = map(
        Path, (package, root, snapshot, previous_receipt, archive))
    target = root / REMOTE_FOLDER
    for path in (package, root, snapshot, target, previous_receipt, archive):
        no_links(path)
    if archive.exists():
        raise FileExistsError('El backup de la prueba debe ser una carpeta NUEVA del PC.')
    if archive.resolve().is_relative_to(root.resolve()):
        raise ValueError('El backup de la prueba no puede estar en la PSP.')
    manifest = verify_package(package)
    old = json.loads(previous_receipt.read_text(encoding='utf-8'))
    if not isinstance(old, dict):
        raise ValueError('Recibo anterior no valido.')
    identity, records = old.get('identity', {}), old.get('files', {})
    if (not isinstance(identity, dict) or not isinstance(records, dict) or
            old.get('installed') is not True or old.get('synthetic_storage') is not True or
            old.get('real_memory_stick_access') is not False or
            old.get('filesystem_writes_only') != REMOTE_FOLDER or
            identity.get('volume_serial') != serial or identity.get('usb_device_id') != usb_id or
            os.path.normcase(os.path.abspath(old.get('target', ''))) !=
            os.path.normcase(os.path.abspath(target)) or set(records) != set(FILES)):
        raise ValueError('El recibo anterior no identifica esta instalacion de prueba y esta PSP.')
    for record in records.values():
        if (not isinstance(record, dict) or type(record.get('size')) is not int or
                record['size'] < 0 or not isinstance(record.get('sha256'), str) or
                not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
            raise ValueError('Recibo anterior sin hashes y tamanos verificables.')
    _verify_tree(target, records)
    matching_backup(root, snapshot, serial, usb_id)
    size = sum(item['size'] for item in manifest['files'].values())
    if shutil.disk_usage(root).free < size + 8 * 1024 * 1024:
        raise ValueError('Falta espacio para preparar la nueva prueba.')
    return {'target': str(target), 'backup': str(snapshot), 'revision': manifest['revision'],
            'files': manifest['files'], 'xmb': manifest['xmb'], 'bytes': size,
            'synthetic_storage': True, 'real_memory_stick_access': False,
            'installed': False, 'hardware_execution_verified': False, 'autorun': False,
            'previous_receipt': str(previous_receipt), 'previous_revision': old.get('revision'),
            'previous_files': records, 'previous_test_archive': str(archive),
            'source_identity': {'volume_serial': serial, 'usb_device_id': usb_id}}


def upgrade_package(package: Path, root: Path, snapshot: Path, serial: str, usb_id: str,
                    previous_receipt: Path, archive: Path, *, same_device) -> dict:
    if not same_device():
        raise ValueError('Cambio la identidad USB antes de preparar la actualizacion.')
    plan = plan_upgrade(package, root, snapshot, serial, usb_id, previous_receipt, archive)
    target, archive = Path(plan['target']), Path(archive)
    token = uuid.uuid4().hex
    staging = target.parent / ('.pspdock-msc-' + token)
    holding = target.parent / ('.pspdock-msc-previous-' + token)
    archive.mkdir(parents=True, exist_ok=False)
    journal = archive / 'upgrade.jsonl'

    def note(phase: str, **extra) -> None:
        with journal.open('a', encoding='utf-8') as writer:
            json.dump({'phase': phase, **extra}, writer, ensure_ascii=False)
            writer.write('\n')
            writer.flush()
            os.fsync(writer.fileno())

    note('planned', **plan, staging=str(staging), holding=str(holding))
    # The previous four files are independently recoverable on the PC before
    # either rename. A failure never triggers deletion on an unverified mount.
    _copy_records(target, archive / 'files', plan['previous_files'])
    _verify_tree(target, plan['previous_files'])
    note('previous_archived')
    if not same_device():
        raise ValueError('Cambio la identidad USB tras guardar la prueba anterior.')
    _copy_records(Path(package) / REMOTE_FOLDER, staging, plan['files'])
    verify_package(package)
    if not same_device():
        raise ValueError('Cambio la identidad USB durante la copia; no se publica.')
    matching_backup(Path(root), Path(snapshot), serial, usb_id)
    _verify_tree(target, plan['previous_files'])
    _verify_tree(archive / 'files', plan['previous_files'])
    if holding.exists():
        raise FileExistsError('Aparecio la carpeta reservada de recuperacion.')
    # Windows cannot replace a nonempty directory in one atomic operation.
    # These are two recoverable same-volume renames, recorded independently.
    target.rename(holding)
    try:
        # If the PC journal fails after the first rename, the original
        # installation still needs the same guarded rollback as publication.
        note('previous_held')
        if not same_device():
            raise ValueError('Cambio la PSP antes de publicar; conservar carpeta de recuperacion.')
        if target.exists():
            raise FileExistsError('Aparecio un destino durante la actualizacion.')
        staging.rename(target)
    except (OSError, ValueError):
        if same_device() and not target.exists():
            _verify_tree(holding, plan['previous_files'])
            holding.rename(target)
            note('previous_restored')
        else:
            note('manual_recovery_required')
        raise
    note('published')
    if not same_device():
        raise ValueError('La PSP se desconecto tras publicar; verificar antes de ejecutar.')
    _verify_tree(target, plan['files'])
    _verify_tree(holding, plan['previous_files'])
    _verify_tree(archive / 'files', plan['previous_files'])
    # Delete only the exact old copies after both the new installation and
    # independent archive are verified. Never recursively delete this tree.
    for name in FILES:
        if not same_device():
            raise ValueError('Cambio la PSP durante la limpieza; conservar el resto del backup.')
        old = holding / name
        no_links(old)
        if digest(old) != plan['previous_files'][name]['sha256']:
            raise ValueError('Cambio un archivo de recuperacion; se conserva: ' + name)
        old.unlink()
    if not same_device():
        raise ValueError('Cambio la PSP antes de retirar la carpeta vacia de recuperacion.')
    holding.rmdir()
    result = {**plan, 'installed': True, 'filesystem_writes_only': REMOTE_FOLDER,
              'upgrade_journal': str(journal), 'previous_archived_and_verified': True}
    note('verified', result=result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Instalar Storage + HID con thumbnail y fondo XMB')
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--drive', required=True)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--receipt', type=Path, help='Archivo NUEVO de evidencia en el PC')
    parser.add_argument('--upgrade-from-receipt', type=Path,
                        help='Recibo previo para actualizar exactamente esa prueba')
    parser.add_argument('--archive', type=Path, help='Carpeta NUEVA del PC para la prueba anterior')
    args = parser.parse_args()
    try:
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('El instalador fisico requiere Windows fuera de las pruebas.')
        identity, _ = identify(args.drive)
        root = Path(identity.drive)
        receipt = None
        if args.receipt:
            from psp_dock.devices.psp_composite_client import _new_local_destination
            receipt = _new_local_destination(args.receipt)
        if args.upgrade_from_receipt:
            if args.archive is None:
                raise ValueError('Actualizar requiere --archive en el PC.')
            from psp_dock.devices.psp_composite_client import _new_local_destination
            _new_local_destination(args.archive / 'upgrade.jsonl')
            parameters = (args.package, root, args.backup, identity.volume_serial,
                          identity.usb_device_id, args.upgrade_from_receipt, args.archive)
            if args.install:
                result = upgrade_package(*parameters,
                    same_device=lambda: identify(identity.drive)[0] == identity)
            else:
                result = plan_upgrade(*parameters)
        elif args.archive:
            raise ValueError('--archive requiere --upgrade-from-receipt.')
        elif args.install:
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
