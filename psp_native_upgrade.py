"""Explicit, recoverable replacement of reviewed native 0300/0301 applications.

Only four pinned files of a known revision may be replaced by audited 0302.
The old application is copied to a new fixed-PC backup before any PSP write.
Directory parking and staging stay outside PSP/GAME; no old launcher can
accidentally load the new PRX from its hard-coded PSPDockNative pathname.
Nothing is deleted, executed or resumed automatically, including on failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from psp_composite_install import matching_backup, no_links
from psp_msc_package import _regular
from psp_native_install import _check_identity, _on_pc, _write_new_json
from psp_native_package import FILES, REMOTE_FOLDER, digest, verify_package
from psp_usb import identify, UsbTransitionError


TO_REVISION = '0302'
REVIEWED_TRANSITIONS = frozenset({('0300', '0301'), ('0300', '0302'), ('0301', '0302')})
SCHEMA = 'pspdock-native-upgrade-receipt-v1'
BACKUP_SCHEMA = 'pspdock-native-application-backup-v1'
OLD_RECEIPT_SHA256 = 'e608ccd22a96bf82c6ae88ddf8a364740c602c40d0c04c66010c51055670dab2'
RECEIPT_0301_SHA256 = 'bc1ab5d87922dc35d5e16aab814ff32d2230749dd501383d91a87353b92cc684'
# Reviewed installation receipt, not hashes inferred from the mounted PSP.
KNOWN_0300_FILES = {
    'EBOOT.PBP': {'size': 301968, 'sha256': '5c981962c4eef43f0ce604df89070776217cb548f57f7e65e4cfc766c81983fd'},
    'docknative.prx': {'size': 21034, 'sha256': 'b3ac8e838e398733898e4db90784b3ed7abf1f661b7893035368d1f299b15d63'},
    'LICENSE-FuSa-GPL-3.0.txt': {'size': 35823, 'sha256': '230184f60bae2feaf244f10a8bac053c8ff33a183bcc365b4d8b876d2b7f4809'},
    'LICENSE-DejaVu.txt': {'size': 2555, 'sha256': '24dc6361beb5925c789c056222029b44093854e5f94e5509b833b5a02d511baf'},
}

# Installed and reread on 2026-11-02, before the observed Sony bcdDevice 0100
# rejection. These anchors come from the retained 0301 upgrade receipt.
KNOWN_0301_FILES = {
    'EBOOT.PBP': {'size': 310072, 'sha256': '955d61312134275de9412177c54495ededde01505af9f36b3b3e9f3b8c371f29'},
    'docknative.prx': {'size': 20938, 'sha256': '607722612f72cf220f4760750d7bc52c2f6224cd39051527c422e246d382caf9'},
    'LICENSE-FuSa-GPL-3.0.txt': {'size': 35823, 'sha256': '230184f60bae2feaf244f10a8bac053c8ff33a183bcc365b4d8b876d2b7f4809'},
    'LICENSE-DejaVu.txt': {'size': 2555, 'sha256': '24dc6361beb5925c789c056222029b44093854e5f94e5509b833b5a02d511baf'},
}


def _known_files(revision: str) -> dict:
    if revision == '0300':
        return KNOWN_0300_FILES
    if revision == '0301':
        return KNOWN_0301_FILES
    raise ValueError('La revisión anterior no pertenece a las aplicaciones verificadas.')


def _reference_receipt(revision: str) -> str:
    _known_files(revision)
    return OLD_RECEIPT_SHA256 if revision == '0300' else RECEIPT_0301_SHA256


def _same(same_device) -> None:
    if not same_device():
        raise ValueError('Cambió la identidad de la PSP; no se iniciarán más escrituras ni limpieza.')


def _records(value) -> dict:
    if (not isinstance(value, dict) or set(value) != set(FILES) or
            any(not isinstance(row, dict) or set(row) != {'size', 'sha256'} or
                type(row.get('size')) is not int or not 1 <= row['size'] <= 4 * 1024 * 1024 or
                not isinstance(row.get('sha256'), str) or
                re.fullmatch('[0-9a-f]{64}', row['sha256']) is None for row in value.values())):
        raise ValueError('El registro no describe exactamente los cuatro archivos nativos.')
    return {name: dict(value[name]) for name in FILES}


def _read_json(path: Path) -> dict:
    no_links(path)
    if not _regular(path) or not 1 <= path.stat().st_size <= 256000:
        raise ValueError('El registro de recuperación no es un archivo regular válido.')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('El registro de recuperación no es un objeto.')
    return value


def _existing_pc(path: Path, root: Path) -> Path:
    """Validate a retained PC path without requiring it to be a new file."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('La evidencia debe estar en una ruta absoluta del PC.')
    no_links(path)
    result = path.resolve()
    if result.is_relative_to(root.resolve()):
        raise ValueError('La copia de recuperación debe estar en el PC, no en la PSP.')
    if os.name == 'nt':
        import ctypes as C
        from ctypes import wintypes as W
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [W.LPCWSTR]
        kernel.GetDriveTypeW.restype = W.UINT
        if kernel.GetDriveTypeW(result.anchor) != 3:
            raise ValueError('La evidencia debe permanecer en un disco fijo del PC.')
    return result


def _observed_files(folder: Path, *, same_device=lambda: True) -> dict:
    _same(same_device)
    no_links(folder)
    if (not folder.is_dir() or getattr(folder.stat(), 'st_file_attributes', 0) & 0x400 or
            {p.name for p in folder.iterdir()} != set(FILES)):
        raise ValueError('La aplicación no contiene exactamente los cuatro archivos conocidos.')
    result = {}
    for name in FILES:
        _same(same_device)
        file = folder / name
        no_links(file)
        if not _regular(file):
            raise ValueError('Archivo de aplicación no regular: ' + name)
        before = file.stat()
        if not 1 <= before.st_size <= 4 * 1024 * 1024:
            raise ValueError('Tamaño de aplicación fuera de límites: ' + name)
        signature = digest(file)
        after = file.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('La aplicación cambió durante su comprobación: ' + name)
        result[name] = {'size': after.st_size, 'sha256': signature}
    _same(same_device)
    if {p.name for p in folder.iterdir()} != set(FILES):
        raise ValueError('La carpeta de aplicación cambió durante su comprobación.')
    return result


def _verify_files(folder: Path, expected: dict, *, same_device=lambda: True) -> None:
    if _observed_files(folder, same_device=same_device) != _records(expected):
        raise ValueError('Los cuatro archivos no coinciden con la versión revisada: ' + str(folder))


def _copy_files(source: Path, target: Path, expected: dict, *, same_device, note) -> None:
    """Create exclusively, flush, then rehash both ends; never clean up."""
    for name in FILES:
        _same(same_device)
        original, copied = source / name, target / name
        no_links(original);no_links(target)
        if not _regular(original):
            raise ValueError('El archivo de origen dejó de ser regular: ' + name)
        with original.open('rb') as reader, copied.open('xb') as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush();os.fsync(writer.fileno())
        _same(same_device)
        no_links(original);no_links(copied)
        record = expected[name]
        if (copied.stat().st_size != record['size'] or original.stat().st_size != record['size'] or
                digest(copied) != record['sha256'] or digest(original) != record['sha256']):
            raise ValueError('La copia o su origen no coinciden con el archivo revisado: ' + name)
        _same(same_device)
        note('file_verified', file=name, destination=str(target), sha256=record['sha256'])


class _Evidence:
    def __init__(self, root: Path, receipt: Path, plan: dict):
        self.receipt = _on_pc(receipt, root)
        self.journal = _on_pc(self.receipt.with_name(self.receipt.name + '.journal.jsonl'), root)
        self.plan = {**plan, 'receipt': str(self.receipt), 'journal': str(self.journal)}
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        no_links(self.receipt.parent)
        _write_new_json(self.receipt, self.plan)
        with self.journal.open('x', encoding='utf-8') as writer:
            writer.flush();os.fsync(writer.fileno())
        self.note('planned', plan=self.plan)

    def note(self, phase: str, **facts) -> None:
        no_links(self.journal)
        with self.journal.open('a', encoding='utf-8') as writer:
            json.dump({'phase': phase, 'utc': datetime.now(timezone.utc).isoformat(), **facts},
                      writer, ensure_ascii=False)
            writer.write('\n');writer.flush();os.fsync(writer.fileno())

    def finish(self, **facts) -> dict:
        result = {**self.plan, **facts, 'verified_utc': datetime.now(timezone.utc).isoformat()}
        self.note('verified', result=result)
        if _read_json(self.receipt) != self.plan:
            raise ValueError('El recibo cambió; se conserva el journal y no se sobrescribe.')
        ready = self.receipt.with_name(self.receipt.name + '.' + uuid.uuid4().hex + '.ready')
        _write_new_json(ready, result)
        ready.replace(self.receipt)
        return result

    def incomplete(self, error: Exception) -> None:
        try:
            self.note('incomplete', error=str(error), recovery_requires_explicit_action=True)
        except OSError:
            pass


def _paths(root: Path, operation: str, operation_id: str) -> tuple[Path, Path, Path]:
    if operation not in ('upgrade', 'rollback') or re.fullmatch('[0-9a-f]{32}', operation_id) is None:
        raise ValueError('Identificador de operación no válido.')
    workspace = root / 'PSP' / ('.pspdock-native-' + operation + '-' + operation_id)
    return workspace, workspace / 'candidate', workspace / 'previous'


def _backup_record(plan: dict) -> dict:
    return {'schema': BACKUP_SCHEMA, 'operation_id': plan['operation_id'],
            'identity': plan['identity'], 'revision': plan['from_revision'],
            'files': plan['from_files'], 'replacement_files': plan['to_files'],
            'reference_installation_receipt_sha256': _reference_receipt(plan['from_revision']), 'complete': True}


def _verify_app_backup(plan: dict, root: Path) -> Path:
    expected = _known_files(plan.get('from_revision'))
    if (plan.get('from_files') != expected or
            plan.get('reference_installation_receipt_sha256') != _reference_receipt(plan['from_revision'])):
        raise ValueError('El origen no coincide con una aplicación y recibo anteriores verificados.')
    backup = _existing_pc(Path(plan['app_backup']), root)
    if not backup.is_dir() or {p.name for p in backup.iterdir()} != {'files', 'manifest.json'}:
        raise ValueError('La copia de recuperación está incompleta o contiene archivos desconocidos.')
    if _read_json(backup / 'manifest.json') != _backup_record(plan):
        raise ValueError('La copia de recuperación no corresponde a esta actualización.')
    _verify_files(backup / 'files', expected)
    return backup / 'files'


def plan_upgrade(package: Path, root: Path, snapshot: Path, serial: str, usb_id: str) -> dict:
    _check_identity(serial, usb_id)
    root, package, snapshot = (Path(p).absolute() for p in (root, package, snapshot))
    for path in (root, root / 'PSP/GAME', package, snapshot):
        no_links(path)
    manifest = verify_package(package)
    if manifest['revision'] != TO_REVISION:
        raise ValueError('La actualización sólo admite el paquete nativo 0302.')
    target = root / REMOTE_FOLDER
    observed = _observed_files(target)
    candidates = [revision for revision in ('0300', '0301')
                  if observed == _records(_known_files(revision))]
    if len(candidates) != 1:
        raise ValueError('La aplicación no coincide con los cuatro archivos revisados de 0300 o 0301.')
    source_revision = candidates[0]
    matching_backup(root, snapshot, serial, usb_id)
    size = sum(record['size'] for record in manifest['files'].values())
    if shutil.disk_usage(root).free < size + 8 * 1024 * 1024:
        raise ValueError('Falta espacio para preparar la aplicación nueva sin borrar la anterior.')
    return {'schema': SCHEMA, 'operation': 'upgrade', 'from_revision': source_revision,
            'to_revision': TO_REVISION, 'target': str(target), 'package': str(package),
            'critical_backup': str(snapshot), 'from_files': _records(_known_files(source_revision)),
            'to_files': _records(manifest['files']), 'bytes': size, 'xmb': manifest['xmb'],
            'identity': {'volume_serial': serial, 'usb_device_id': usb_id},
            'installed': False, 'hardware_execution_verified': False, 'autorun': False,
            'reference_installation_receipt_sha256': _reference_receipt(source_revision)}


def upgrade_package(package: Path, root: Path, snapshot: Path, serial: str, usb_id: str,
                    *, same_device, receipt: Path, app_backup: Path) -> dict:
    _same(same_device)
    plan = plan_upgrade(package, root, snapshot, serial, usb_id)
    root, package = Path(root).absolute(), Path(package).absolute()
    receipt, app_backup = _on_pc(Path(receipt), root), _on_pc(Path(app_backup), root)
    for destination in (receipt, app_backup):
        if any(destination.is_relative_to(Path(source).resolve())
               for source in (package, snapshot)):
            raise ValueError('No se guarda evidencia dentro del paquete ni de otro backup.')
    if receipt.is_relative_to(app_backup) or app_backup.is_relative_to(receipt):
        raise ValueError('El recibo y la copia de aplicación deben tener destinos separados.')
    operation_id = uuid.uuid4().hex
    workspace, staging, parked = _paths(root, 'upgrade', operation_id)
    plan.update(operation_id=operation_id, app_backup=str(app_backup),
                workspace=str(workspace), staging=str(staging), parked=str(parked),
                filesystem_writes_only=[REMOTE_FOLDER, workspace.relative_to(root).as_posix()])
    evidence = _Evidence(root, receipt, plan)
    target = root / REMOTE_FOLDER
    try:
        _same(same_device)
        _verify_files(target, plan['from_files'], same_device=same_device)
        app_backup.mkdir(parents=True, exist_ok=False)
        saved = app_backup / 'files';saved.mkdir()
        _copy_files(target, saved, plan['from_files'], same_device=same_device, note=evidence.note)
        _write_new_json(app_backup / 'manifest.json', _backup_record(plan))
        _verify_app_backup(plan, root)
        evidence.note('application_backup_verified', backup=str(app_backup))
        # The first PSP mutation happens only after the retained PC backup
        # is complete, reread and tied to this device and these four files.
        _same(same_device)
        no_links(workspace);workspace.mkdir(exist_ok=False)
        staging.mkdir(exist_ok=False)
        _copy_files(package / REMOTE_FOLDER, staging, plan['to_files'],
                    same_device=same_device, note=evidence.note)
        if verify_package(package)['files'] != plan['to_files']:
            raise ValueError('El paquete cambió durante la preparación.')
        _verify_app_backup(plan, root)
        _same(same_device)
        matching_backup(root, Path(snapshot), serial, usb_id)
        _verify_files(staging, plan['to_files'], same_device=same_device)
        _verify_files(target, plan['from_files'], same_device=same_device)
        no_links(parked)
        if parked.exists():
            raise FileExistsError('Apareció el destino de aparcado; no se sobrescribe.')
        evidence.note('ready_to_publish', target=str(target), parked=str(parked))
        _same(same_device)
        target.rename(parked)
        evidence.note('old_application_parked')
        _same(same_device)
        no_links(target);no_links(staging)
        if target.exists():
            raise FileExistsError('Apareció otra aplicación en el destino; no se sobrescribe.')
        staging.rename(target)
        evidence.note('published')
        _verify_files(target, plan['to_files'], same_device=same_device)
        _verify_files(parked, plan['from_files'], same_device=same_device)
        _verify_app_backup(plan, root)
        _same(same_device)
        return evidence.finish(installed=True, revision=TO_REVISION, previous_application_preserved=True)
    except Exception as exc:
        evidence.incomplete(exc)
        raise


def _upgrade_record(receipt: Path, root: Path, serial: str, usb_id: str) -> dict:
    _check_identity(serial, usb_id)
    saved = _read_json(_existing_pc(receipt, root))
    previous, replacement = saved.get('from_revision'), saved.get('to_revision')
    if (saved.get('schema') != SCHEMA or saved.get('operation') != 'upgrade' or
            not isinstance(previous, str) or not isinstance(replacement, str) or
            (previous, replacement) not in REVIEWED_TRANSITIONS or
            saved.get('identity') != {'volume_serial': serial, 'usb_device_id': usb_id} or
            saved.get('from_files') != _known_files(previous) or
            saved.get('reference_installation_receipt_sha256') != _reference_receipt(previous) or
            not isinstance(saved.get('operation_id'), str) or
            re.fullmatch('[0-9a-f]{32}', saved['operation_id']) is None or
            not isinstance(saved.get('app_backup'), str)):
        raise ValueError('El recibo no pertenece a una actualización conocida de esta PSP.')
    _records(saved.get('to_files'))
    if replacement == '0301' and saved['to_files'] != _known_files('0301'):
        raise ValueError('El recibo histórico no conserva la aplicación 0301 verificada.')
    _verify_app_backup(saved, root)
    return saved


def _target_state(root: Path, saved: dict, *, same_device=lambda: True) -> str:
    target = root / REMOTE_FOLDER
    _same(same_device);no_links(target)
    if not target.exists():
        return 'absent'
    files = _observed_files(target, same_device=same_device)
    if files == saved['from_files']:
        return 'original'
    if files == saved['to_files']:
        return 'replacement'
    raise ValueError('El destino contiene otros archivos; no se restaura sobre una aplicación desconocida.')


def plan_rollback(upgrade_receipt: Path, root: Path, serial: str, usb_id: str) -> dict:
    root = Path(root).absolute();no_links(root);no_links(root / 'PSP/GAME')
    if not (root / 'PSP/GAME').is_dir():
        raise ValueError('Falta la carpeta PSP/GAME de la consola identificada.')
    saved = _upgrade_record(Path(upgrade_receipt), root, serial, usb_id)
    state = _target_state(root, saved)
    if state != 'original' and shutil.disk_usage(root).free < sum(
            row['size'] for row in saved['from_files'].values()) + 8 * 1024 * 1024:
        raise ValueError('Falta espacio para preparar la restauración sin borrar archivos.')
    return {'schema': 'pspdock-native-rollback-receipt-v1', 'operation': 'rollback',
            'upgrade_receipt': str(Path(upgrade_receipt).absolute()),
            'upgrade_operation_id': saved['operation_id'], 'identity': saved['identity'],
            'target': str(root / REMOTE_FOLDER), 'target_state': state,
            'app_backup': saved['app_backup'], 'restore_files': _records(saved['from_files']),
            'replaced_files': saved['to_files'], 'restored': False, 'revision': saved['from_revision'],
            'retired_revision': True, 'hardware_execution_verified': False, 'autorun': False}


def rollback_package(upgrade_receipt: Path, root: Path, serial: str, usb_id: str,
                     *, same_device, receipt: Path) -> dict:
    _same(same_device)
    root = Path(root).absolute()
    plan = plan_rollback(upgrade_receipt, root, serial, usb_id)
    receipt = _on_pc(Path(receipt), root)
    if receipt.is_relative_to(Path(plan['app_backup']).resolve()):
        raise ValueError('El recibo de restauración debe estar fuera de la copia conservada.')
    operation_id = uuid.uuid4().hex
    workspace, staging, parked = _paths(root, 'rollback', operation_id)
    plan.update(operation_id=operation_id, workspace=str(workspace), staging=str(staging),
                parked=str(parked), filesystem_writes_only=[] if plan['target_state']=='original'
                else [REMOTE_FOLDER, workspace.relative_to(root).as_posix()])
    evidence = _Evidence(root, receipt, plan)
    target = root / REMOTE_FOLDER
    try:
        saved = _upgrade_record(Path(upgrade_receipt), root, serial, usb_id)
        if _target_state(root, saved, same_device=same_device) != plan['target_state']:
            raise ValueError('La aplicación cambió antes de restaurar.')
        if plan['target_state'] == 'original':
            _same(same_device)
            return evidence.finish(restored=True, already_original=True)
        original = _verify_app_backup(saved, root)
        _same(same_device)
        no_links(workspace);workspace.mkdir(exist_ok=False)
        staging.mkdir(exist_ok=False)
        _copy_files(original, staging, plan['restore_files'],
                    same_device=same_device, note=evidence.note)
        _verify_app_backup(saved, root)
        _verify_files(staging, plan['restore_files'], same_device=same_device)
        if _target_state(root, saved, same_device=same_device) != plan['target_state']:
            raise ValueError('La aplicación cambió antes de publicar la restauración.')
        no_links(parked)
        if parked.exists():
            raise FileExistsError('Apareció el destino de aparcado; no se sobrescribe.')
        evidence.note('ready_to_restore')
        if plan['target_state'] == 'replacement':
            _same(same_device)
            target.rename(parked)
            evidence.note('replacement_parked')
        _same(same_device)
        no_links(target);no_links(staging)
        if target.exists():
            raise FileExistsError('Apareció otra aplicación en el destino; no se sobrescribe.')
        staging.rename(target)
        evidence.note('restored')
        _verify_files(target, plan['restore_files'], same_device=same_device)
        if plan['target_state'] == 'replacement':
            _verify_files(parked, saved['to_files'], same_device=same_device)
        _same(same_device)
        return evidence.finish(restored=True, already_original=False)
    except Exception as exc:
        evidence.incomplete(exc)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Actualizar sólo PSP Dock 0300/0301 conocido a 0302, o restaurar su versión anterior explícitamente')
    parser.add_argument('--drive', required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--upgrade', action='store_true', help='Ejecutar la actualización; por defecto sólo se muestra el plan')
    action.add_argument('--rollback', type=Path, metavar='UPGRADE_RECEIPT', help='Planificar la restauración desde este recibo')
    parser.add_argument('--restore', action='store_true', help='Ejecutar la restauración pedida con --rollback')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--backup', type=Path, help='Backup crítico actual de la misma PSP')
    parser.add_argument('--app-backup', type=Path, help='Directorio NUEVO en disco fijo del PC para los cuatro archivos anteriores')
    parser.add_argument('--receipt', type=Path, help='Archivo NUEVO del PC, obligatorio al ejecutar')
    args = parser.parse_args(argv)
    try:
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('La actualización física requiere Windows fuera de las pruebas.')
        if args.restore and not args.rollback:
            raise ValueError('--restore requiere --rollback con el recibo de actualización.')
        if (args.upgrade or args.restore) and args.receipt is None:
            raise ValueError('La ejecución requiere un --receipt nuevo en el PC.')
        if not args.rollback and (args.package is None or args.backup is None or
                                  args.upgrade and args.app_backup is None):
            raise ValueError('Indica --package y --backup; --upgrade también requiere --app-backup.')
        device, _ = identify(args.drive)
        _check_identity(device.volume_serial, device.usb_device_id)
        same_device = lambda: identify(device.drive)[0] == device
        params = (Path(device.drive), device.volume_serial, device.usb_device_id)
        if args.rollback:
            result = (rollback_package(args.rollback, *params, same_device=same_device, receipt=args.receipt)
                      if args.restore else plan_rollback(args.rollback, *params))
        else:
            params = (args.package, Path(device.drive), args.backup, device.volume_serial, device.usb_device_id)
            result = (upgrade_package(*params, same_device=same_device, receipt=args.receipt, app_backup=args.app_backup)
                      if args.upgrade else plan_upgrade(*params))
        print(json.dumps(result, ensure_ascii=False, indent=2));return 0
    except (OSError, ValueError, KeyError, UsbTransitionError) as exc:
        print('Actualización/restauración nativa no verificada:', str(exc));return 1


if __name__ == '__main__':
    raise SystemExit(main())
