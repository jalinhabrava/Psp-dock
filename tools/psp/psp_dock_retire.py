"""Retire explicitly reviewed old PSP Dock applications from Sony XMB USB.

Only PSP/GAME/PSPDock and PSP/GAME/PSPDockStorageTest are eligible. The
current PSPDockNative installation must match the audited 0302 package.
The complete reviewed contents of every selected application are copied,
flushed and rehashed on the PC before the first deletion on the PSP.
An interruption preserves the backup and journal; nothing resumes itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from tools.psp.psp_composite_install import no_links
from tools.psp.psp_msc_package import _regular
from tools.psp.psp_native_install import _check_identity, _on_pc, _write_new_json
from tools.psp.psp_native_package import REMOTE_FOLDER, digest, verify_package
from tools.psp.psp_native_upgrade import _Evidence, _existing_pc, _read_json, _same
from psp_dock.devices.psp_usb import identify, UsbTransitionError


ALLOWLIST = frozenset({'PSP/GAME/PSPDock', 'PSP/GAME/PSPDockStorageTest'})
REVIEW_SCHEMA = 'pspdock-retirement-review-v1'
RECEIPT_SCHEMA = 'pspdock-retirement-receipt-v1'
BACKUP_SCHEMA = 'pspdock-retirement-backup-v1'
MAX_FILE_BYTES = 64 * 1024 * 1024
RESERVED_NAMES = frozenset({'CON', 'PRN', 'AUX', 'NUL'} |
                           {prefix + str(n) for prefix in ('COM', 'LPT') for n in range(1, 10)})


def _files(value) -> dict:
    if not isinstance(value, dict) or not 1 <= len(value) <= 64:
        raise ValueError('Cada aplicación necesita un conjunto explícito de archivos revisados.')
    if len({str(name).casefold() for name in value}) != len(value):
        raise ValueError('El plan contiene nombres que se confunden en Windows.')
    for name, row in value.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._ -]{0,127}', name) or
                name.endswith((' ', '.')) or name.split('.')[0].upper() in RESERVED_NAMES or
                not isinstance(row, dict) or set(row) != {'size', 'sha256'} or
                type(row['size']) is not int or not 0 <= row['size'] <= MAX_FILE_BYTES or
                not isinstance(row['sha256'], str) or re.fullmatch('[0-9a-f]{64}', row['sha256']) is None):
            raise ValueError('Archivo revisado inválido; no se admiten rutas ni subcarpetas.')
    return {name: dict(value[name]) for name in sorted(value)}


def _review(value: dict, serial: str, usb_id: str) -> dict:
    _check_identity(serial, usb_id)
    if (not isinstance(value, dict) or value.get('schema') != REVIEW_SCHEMA or
            value.get('reviewed') is not True or
            value.get('identity') != {'volume_serial': serial, 'usb_device_id': usb_id} or
            not isinstance(value.get('applications'), dict) or not value['applications'] or
            not set(value['applications']).issubset(ALLOWLIST)):
        raise ValueError('Se necesita un plan revisado para estas aplicaciones y esta PSP.')
    applications = {}
    for folder, record in sorted(value['applications'].items()):
        if not isinstance(record, dict) or set(record) != {'files'}:
            raise ValueError('Cada aplicación debe declarar exactamente sus archivos revisados.')
        applications[folder] = {'files': _files(record['files'])}
    return {'schema': REVIEW_SCHEMA, 'reviewed': True, 'identity': dict(value['identity']),
            'applications': applications}


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _file_record(file: Path, *, same_device) -> dict:
    _same(same_device);no_links(file)
    if not _regular(file):
        raise ValueError('No se retira un archivo no regular: ' + file.name)
    before = file.stat()
    if not 0 <= before.st_size <= MAX_FILE_BYTES:
        raise ValueError('El archivo supera el tamaño permitido: ' + file.name)
    signature = digest(file)
    no_links(file)
    after = file.stat()
    _same(same_device)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('El archivo cambió durante la comprobación: ' + file.name)
    return {'size': after.st_size, 'sha256': signature}


def _verify_folder(folder: Path, expected: dict, *, same_device) -> None:
    _same(same_device);no_links(folder)
    if (not folder.is_dir() or getattr(folder.stat(), 'st_file_attributes', 0) & 0x400 or
            {path.name for path in folder.iterdir()} != set(expected)):
        raise ValueError('La carpeta cambió o contiene archivos o subcarpetas no revisados: ' + folder.name)
    for name, record in expected.items():
        if _file_record(folder / name, same_device=same_device) != record:
            raise ValueError('El contenido ya no coincide con el plan revisado: ' + name)
    _same(same_device);no_links(folder)
    if {path.name for path in folder.iterdir()} != set(expected):
        raise ValueError('La carpeta cambió durante la comprobación: ' + folder.name)


def _current_application(package: Path, root: Path, *, same_device, expected=None) -> dict:
    _same(same_device)
    manifest = verify_package(package)
    if manifest['revision'] != '0302':
        raise ValueError('Primero debe estar instalado el paquete actual de PSP Dock.')
    records = _files(manifest['files'])
    if expected is not None and records != expected:
        raise ValueError('El paquete de PSP Dock cambió durante la retirada.')
    _verify_folder(root / REMOTE_FOLDER, records, same_device=same_device)
    return records


def plan_retirement(package: Path, root: Path, reviewed_plan: dict, serial: str, usb_id: str,
                    *, same_device=lambda: True) -> dict:
    """Read-only: never infer deletion authority from current PSP contents."""
    checked = _review(reviewed_plan, serial, usb_id)
    root, package = Path(root).absolute(), Path(package).absolute()
    for path in (root, root / 'PSP/GAME', package):
        no_links(path)
    current = _current_application(package, root, same_device=same_device)
    for folder, record in checked['applications'].items():
        _verify_folder(root / folder, record['files'], same_device=same_device)
    return {'schema': RECEIPT_SCHEMA, 'operation': 'retire_old_dock',
            'identity': checked['identity'], 'reviewed_plan': checked,
            'reviewed_plan_fingerprint': _fingerprint(checked),
            'applications': checked['applications'], 'package': str(package),
            'current_application': {'folder': REMOTE_FOLDER, 'revision': '0302', 'files': current},
            'file_count': sum(len(item['files']) for item in checked['applications'].values()),
            'bytes': sum(row['size'] for item in checked['applications'].values() for row in item['files'].values()),
            'filesystem_deletions_only': sorted(checked['applications']),
            'retired': False, 'backup_verified': False, 'autorun': False,
            'hardware_execution_verified': False, 'safe_disconnect_verified': False}


def _backup_manifest(plan: dict) -> dict:
    return {'schema': BACKUP_SCHEMA, 'operation_id': plan['operation_id'], 'identity': plan['identity'],
            'reviewed_plan_fingerprint': plan['reviewed_plan_fingerprint'],
            'applications': plan['applications'], 'current_application': plan['current_application'],
            'file_count': plan['file_count'], 'bytes': plan['bytes'], 'complete': True}


def _verify_backup(backup: Path, plan: dict, root: Path) -> None:
    backup = _existing_pc(backup, root)
    no_links(backup)
    if (not backup.is_dir() or {path.name for path in backup.iterdir()} != {'PSP', 'manifest.json'} or
            _read_json(backup / 'manifest.json') != _backup_manifest(plan)):
        raise ValueError('La copia conservada en el PC está incompleta o cambió.')
    for parent, children in ((backup / 'PSP', {'GAME'}),
                             (backup / 'PSP/GAME', {Path(folder).name for folder in plan['applications']})):
        no_links(parent)
        if not parent.is_dir() or {path.name for path in parent.iterdir()} != children:
            raise ValueError('La copia del PC contiene un árbol inesperado.')
    for folder, record in plan['applications'].items():
        _verify_folder(backup / folder, record['files'], same_device=lambda: True)


def _copy_backup(root: Path, backup: Path, plan: dict, *, same_device, evidence) -> None:
    backup.mkdir(parents=True, exist_ok=False)
    no_links(backup)
    no_links(backup / 'PSP/GAME')
    (backup / 'PSP/GAME').mkdir(parents=True, exist_ok=False)
    for folder, record in plan['applications'].items():
        target = backup / folder
        no_links(target)
        target.mkdir(exist_ok=False)
        for name, expected in record['files'].items():
            source, copied = root / folder / name, target / name
            _same(same_device);no_links(source);no_links(target)
            if _file_record(source, same_device=same_device) != expected:
                raise ValueError('La aplicación cambió antes de copiarla: ' + name)
            with source.open('rb') as reader, copied.open('xb') as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush();os.fsync(writer.fileno())
            _same(same_device)
            if (_file_record(copied, same_device=lambda: True) != expected or
                    _file_record(source, same_device=same_device) != expected):
                raise ValueError('No se verificó la copia de seguridad: ' + name)
            evidence.note('backup_file_verified', folder=folder, file=name, sha256=expected['sha256'])
    no_links(backup / 'manifest.json')
    _write_new_json(backup / 'manifest.json', _backup_manifest(plan))
    _verify_backup(backup, plan, root)
    evidence.note('backup_verified', backup=str(backup), file_count=plan['file_count'], bytes=plan['bytes'])


def retire_applications(package: Path, root: Path, reviewed_plan: dict, serial: str, usb_id: str,
                        *, same_device, backup: Path, receipt: Path) -> dict:
    _same(same_device)
    root, package = Path(root).absolute(), Path(package).absolute()
    plan = plan_retirement(package, root, reviewed_plan, serial, usb_id, same_device=same_device)
    backup, receipt = _on_pc(Path(backup), root), _on_pc(Path(receipt), root)
    if backup.is_relative_to(receipt) or receipt.is_relative_to(backup):
        raise ValueError('El recibo y la copia deben tener destinos separados.')
    if any(path.is_relative_to(package.resolve()) for path in (backup, receipt)):
        raise ValueError('No se guarda evidencia dentro del paquete aprobado.')
    space_root = backup.parent
    while not space_root.exists():
        space_root = space_root.parent
    if shutil.disk_usage(space_root).free < plan['bytes'] + 8 * 1024 * 1024:
        raise ValueError('No hay espacio suficiente en el PC para conservar las aplicaciones.')
    plan.update(operation_id=uuid.uuid4().hex, backup=str(backup))
    evidence = _Evidence(root, receipt, plan)
    try:
        _copy_backup(root, backup, plan, same_device=same_device, evidence=evidence)
        expected_current = plan['current_application']['files']
        _current_application(package, root, same_device=same_device, expected=expected_current)
        for folder, record in plan['applications'].items():
            _verify_folder(root / folder, record['files'], same_device=same_device)
        evidence.note('ready_to_retire', folders=sorted(plan['applications']))
        for folder, record in plan['applications'].items():
            remaining = dict(record['files'])
            # Remove the launcher first, so a partial retirement cannot be
            # launched with a missing PRX. Never delete an unreviewed file.
            for name in sorted(remaining, key=lambda item: (item.upper() != 'EBOOT.PBP', item)):
                _current_application(package, root, same_device=same_device, expected=expected_current)
                _verify_backup(backup, plan, root)
                _verify_folder(root / folder, remaining, same_device=same_device)
                evidence.note('retiring_file', folder=folder, file=name, sha256=remaining[name]['sha256'])
                _same(same_device)
                no_links(root / folder / name)
                (root / folder / name).unlink()
                evidence.note('file_retired', folder=folder, file=name)
                remaining.pop(name)
                _same(same_device)
            _current_application(package, root, same_device=same_device, expected=expected_current)
            _verify_backup(backup, plan, root)
            _verify_folder(root / folder, {}, same_device=same_device)
            evidence.note('retiring_empty_directory', folder=folder)
            _same(same_device);no_links(root / folder)
            (root / folder).rmdir()
            evidence.note('directory_retired', folder=folder)
            _same(same_device)
        _current_application(package, root, same_device=same_device, expected=expected_current)
        _verify_backup(backup, plan, root)
        for folder in plan['applications']:
            _same(same_device);no_links(root / folder)
            if (root / folder).exists():
                raise ValueError('Una carpeta retirada reapareció; revisa el resultado.')
        return evidence.finish(retired=True, backup_verified=True, current_application_verified=True)
    except Exception as exc:
        try:
            evidence.incomplete(exc)
        except (OSError, ValueError):
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Retirar versiones antiguas revisadas de PSP Dock conservando una copia verificada')
    parser.add_argument('--drive', required=True)
    parser.add_argument('--package', required=True, type=Path, help='Paquete aprobado de PSP Dock actual')
    parser.add_argument('--reviewed-plan', required=True, type=Path, help='JSON revisado en el PC con identidad y hashes exactos')
    parser.add_argument('--retire', action='store_true', help='Ejecutar la retirada; por defecto sólo se verifica el plan')
    parser.add_argument('--backup', type=Path, help='Directorio NUEVO del PC que conservará las aplicaciones completas')
    parser.add_argument('--receipt', type=Path, help='Archivo NUEVO del PC para recibo y journal')
    args = parser.parse_args(argv)
    try:
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('La retirada física requiere Windows fuera de las pruebas.')
        if args.retire and (args.backup is None or args.receipt is None):
            raise ValueError('--retire requiere --backup y --receipt nuevos en el PC.')
        device, _ = identify(args.drive)
        _check_identity(device.volume_serial, device.usb_device_id)
        root = Path(device.drive)
        approved = _read_json(_existing_pc(args.reviewed_plan, root))
        same_device = lambda: identify(device.drive)[0] == device
        params = (args.package, root, approved, device.volume_serial, device.usb_device_id)
        result = (retire_applications(*params, same_device=same_device, backup=args.backup, receipt=args.receipt)
                  if args.retire else plan_retirement(*params, same_device=same_device))
        print(json.dumps(result, ensure_ascii=False, indent=2));return 0
    except (OSError, ValueError, KeyError, UsbTransitionError) as exc:
        print('Retirada de PSP Dock no verificada:', str(exc));return 1


if __name__ == '__main__':
    raise SystemExit(main())
