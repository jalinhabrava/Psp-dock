"""Focused opaque backup of PSP emulator saves, always PSP -> a new PC folder.

This supplements the existing critical backup, whose scope does not cover
emulator data in PSP/GAME. It copies bytes without converting them or claiming
that a PSP savestate can run in a Windows emulator. No ROM/program/BIOS payload
is copied, and no file on the PSP is written, renamed or deleted.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

from tools.psp.psp_backup import copy_verified
from psp_dock.devices.psp_composite_client import _new_local_destination
from psp_dock.emulation.psp_retro import EXTENSIONS, emulator_profile
from psp_dock.devices.psp_usb import identify


SCHEMA = 'pspdock-retro-backup-v1'
INCOMPLETE = 'BACKUP-INCOMPLETE.txt'
MAX_ENTRIES = 16384
MAX_FILES = 4096
MAX_DEPTH = 8
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
RESERVE_BYTES = 64 * 1024 * 1024
SAVE_DIRS = frozenset(('sav', 'save', 'saves', 'saveram', 'sram', 'ss', 'state',
                      'states', 'savestate', 'savestates', 'savegames', 'savedata'))
SAVE_EXTENSIONS = frozenset(('.sav', '.srm', '.svs', '.state', '.sta', '.st', '.frz',
                              '.fcs', '.eep', '.sra', '.mpk', '.fla', '.nvr', '.nv'))
EXCLUDED_EXTENSIONS = frozenset(EXTENSIONS) | frozenset((
    '.zip', '.7z', '.rar', '.gz', '.bz2', '.xz', '.tar', '.iso', '.cso', '.chd',
    '.pbp', '.prx', '.elf', '.exe', '.dll', '.bat', '.cmd', '.com', '.lnk',
    '.bin', '.rom', '.bios', '.img', '.cue', '.wad', '.d64', '.adf',
))
EXCLUDED_DIRS = frozenset(('bios', 'system', 'cores', 'plugins'))
_NUMBERED_STATE = re.compile(r'^\.(?:[0-9]{3}|s[0-9]{2}|st[0-9]{1,2}|state[0-9]+|sv[0-9]+|fcs[0-9]+)$', re.I)
_USB_ID = re.compile(r'^USB\\VID_054C&PID_[0-9A-F]{4}\\[^\\]+$', re.I)
_SHA = re.compile(r'^[0-9a-f]{64}$')


class RetroBackupError(ValueError):
    pass


def _identity(serial: str, usb_id: str) -> None:
    if not isinstance(serial, str) or not serial.isdecimal() or not 0 <= int(serial) <= 0xFFFFFFFF:
        raise RetroBackupError('Serial de Memory Stick inválido.')
    if not isinstance(usb_id, str) or not _USB_ID.fullmatch(usb_id):
        raise RetroBackupError('Se requiere la identidad física USB Sony exacta.')


def _same(callback) -> None:
    if not callable(callback) or callback() is not True:
        raise RetroBackupError('La identidad de la PSP cambió o no se pudo comprobar.')


def _normal(name: str) -> str:
    return re.sub('[^a-z0-9]', '', name.casefold())


def _no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and
                getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise RetroBackupError('El respaldo no admite enlaces ni puntos de reanálisis: ' + str(item))


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value or
            '\0' in value or value.startswith('/') or
            any(part in ('', '.', '..') or part.endswith((' ', '.'))
                for part in value.split('/'))):
        raise RetroBackupError('Ruta relativa del respaldo no segura.')
    path = PurePosixPath(value)
    if path.as_posix() != value or len(path.parts) < 4 or tuple(path.parts[:2]) != ('PSP', 'GAME'):
        raise RetroBackupError('El respaldo retro sólo contiene archivos bajo PSP/GAME/<emulador>.')
    return value


def _safe(root: Path, path: Path) -> Path:
    path = Path(path).absolute()
    if not path.is_relative_to(root):
        raise RetroBackupError('Ruta fuera del origen del respaldo.')
    _no_links(path)
    return path


def selection(root: Path) -> dict:
    """Bounded names/metadata scan; only emulator EBOOT metadata is read here."""
    root = Path(root).absolute()
    game_root = _safe(root, root / 'PSP' / 'GAME')
    if not game_root.is_dir():
        raise RetroBackupError('Falta PSP/GAME en el origen.')
    report = {'files': {}, 'emulators': [], 'unclassified_count': 0,
              'unclassified_sample': [], 'excluded_count': 0,
              'excluded_in_save_dirs': [], 'size_bytes': 0}
    seen, case_paths, count = set(), set(), 0
    folders = sorted(game_root.iterdir(), key=lambda path: path.name.casefold())
    if len(folders) > MAX_ENTRIES:
        raise RetroBackupError('Demasiadas entradas en PSP/GAME.')
    for folder in folders:
        _safe(root, folder)
        if not folder.is_dir():
            continue
        eboot = _safe(root, folder / 'EBOOT.PBP')
        if not eboot.is_file():
            continue
        profile = emulator_profile(folder, eboot)
        if not profile:
            continue
        if len(report['emulators']) >= 64:
            raise RetroBackupError('Demasiados emuladores para el respaldo focal.')
        emulator_path = folder.relative_to(root).as_posix()
        if emulator_path.casefold() in seen:
            raise RetroBackupError('Nombres de emulador ambiguos por mayúsculas.')
        seen.add(emulator_path.casefold())
        report['emulators'].append({'relative': emulator_path, **profile})
        for current, dirs, names in os.walk(folder, followlinks=False):
            base = _safe(root, Path(current))
            count += len(dirs) + len(names)
            if count > MAX_ENTRIES:
                raise RetroBackupError('El inventario retro supera el límite de entradas.')
            for name in dirs:
                _safe(root, base / name)
            dirs[:] = sorted(name for name in dirs if _normal(name) not in EXCLUDED_DIRS)
            if len(base.relative_to(folder).parts) >= MAX_DEPTH and dirs:
                raise RetroBackupError('El inventario retro supera la profundidad permitida.')
            in_save_dir = any(_normal(part) in SAVE_DIRS for part in base.relative_to(folder).parts)
            for name in sorted(names, key=str.casefold):
                source = _safe(root, base / name)
                if not source.is_file():
                    raise RetroBackupError('Entrada no regular en el emulador: ' + str(source))
                relative = _relative(source.relative_to(root).as_posix())
                if relative.casefold() in case_paths:
                    raise RetroBackupError('Rutas retro ambiguas por mayúsculas: ' + relative)
                case_paths.add(relative.casefold())
                suffix = source.suffix.casefold()
                inner_suffix = source.with_suffix('').suffix.casefold() if suffix == '.gz' else ''
                # Some PSP emulators store compressed states, e.g. MasterBoy
                # SAVE/game.sv0.gz. Archive the original bytes only, and only
                # when both the directory and the inner save suffix agree.
                # ROMs such as game.gba.gz and generic .gz remain excluded.
                compressed_save = bool(suffix == '.gz' and in_save_dir and
                    (inner_suffix in SAVE_EXTENSIONS or _NUMBERED_STATE.fullmatch(inner_suffix)))
                if suffix in EXCLUDED_EXTENSIONS and not compressed_save:
                    report['excluded_count'] += 1
                    if in_save_dir and len(report['excluded_in_save_dirs']) < 32:
                        report['excluded_in_save_dirs'].append(relative)
                    continue
                reason = ('compressed_save_opaque' if compressed_save else
                          'save_directory' if in_save_dir else
                          'save_extension' if suffix in SAVE_EXTENSIONS or _NUMBERED_STATE.fullmatch(suffix) else None)
                if reason is None:
                    report['unclassified_count'] += 1
                    if len(report['unclassified_sample']) < 32:
                        report['unclassified_sample'].append(relative)
                    continue
                stat = source.stat()
                if stat.st_size > MAX_FILE_BYTES:
                    raise RetroBackupError('Un archivo de guardado supera 64 MiB: ' + relative)
                report['size_bytes'] += stat.st_size
                if len(report['files']) >= MAX_FILES or report['size_bytes'] > MAX_TOTAL_BYTES:
                    raise RetroBackupError('Los guardados seleccionados superan el límite del respaldo focal.')
                report['files'][relative] = {'size': stat.st_size, 'modified_ns': stat.st_mtime_ns,
                    'emulator': profile['name'], 'emulator_folder': emulator_path, 'reason': reason}
    report['file_count'] = len(report['files'])
    report['scope'] = 'opaque_emulator_save_files'
    report['savestate_compatibility_verified'] = False
    report['writes_to_psp'] = False
    return report


def _hash(path: Path) -> str:
    _no_links(path)
    with path.open('rb') as reader:
        return hashlib.file_digest(reader, 'sha256').hexdigest()


def _write_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as writer:
        json.dump(value, writer, ensure_ascii=False, indent=2, sort_keys=True)
        writer.write('\n')
        writer.flush()
        os.fsync(writer.fileno())


def backup(root: Path, destination: Path, serial: str, usb_id: str, *, same_device,
           reserve_bytes: int = RESERVE_BYTES) -> dict:
    """Create exactly one new PC archive; leave an incomplete marker on error."""
    root = Path(root).absolute()
    _identity(serial, usb_id)
    _same(same_device)
    before = selection(root)
    target = _new_local_destination(Path(destination))
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise RetroBackupError('El respaldo debe estar separado de la Memory Stick.')
    target.parent.mkdir(parents=True, exist_ok=True)
    _no_links(target.parent)
    if shutil.disk_usage(target.parent).free < before['size_bytes'] + reserve_bytes:
        raise RetroBackupError('No hay espacio libre suficiente en el PC.')
    _same(same_device)
    target.mkdir(exist_ok=False)
    marker = target / INCOMPLETE
    with marker.open('x', encoding='utf-8') as writer:
        writer.write('RESPALDO INCOMPLETO: no usar como evidencia de copia verificada.\n')
        writer.flush()
        os.fsync(writer.fileno())
    (target / 'data').mkdir()
    manifest = {'schema': SCHEMA, 'status': 'incomplete',
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'volume_serial': serial, 'usb_device_id': usb_id,
        'scope': 'opaque_emulator_save_files', 'writes_to_psp': False,
        'savestate_compatibility_verified': False, 'selection': before,
        'file_count': before['file_count'], 'size_bytes': before['size_bytes'], 'files': {}}
    # No cleanup in an exception path: retaining partial PC bytes aids recovery.
    for relative, record in before['files'].items():
        _same(same_device)
        source = _safe(root, root.joinpath(*PurePosixPath(relative).parts))
        copied = copy_verified(source, target / 'data' / relative, record['size'])
        _same(same_device)
        manifest['files'][relative] = {**record, **copied}
    _same(same_device)
    if selection(root) != before:
        raise RetroBackupError('Los archivos retro cambiaron durante el respaldo.')
    # Fresh source hashes catch a change to an earlier file during later copies.
    for relative, record in manifest['files'].items():
        _same(same_device)
        if _hash(_safe(root, root / relative)) != record['sha256']:
            raise RetroBackupError('Un guardado PSP cambió después de copiarse: ' + relative)
    _same(same_device)
    manifest['status'] = 'verified'
    _write_json(target / 'manifest.json', manifest)
    # Verify copied bytes independently before removing the incomplete marker.
    _verify(target, allow_incomplete=True)
    _same(same_device)
    marker.unlink()
    return {'directory': str(target), 'status': 'verified', 'file_count': before['file_count'],
            'size_bytes': before['size_bytes'], 'volume_serial': serial, 'usb_device_id': usb_id,
            'writes_to_psp': False, 'savestate_compatibility_verified': False}


def _verify(snapshot: Path, *, allow_incomplete: bool = False) -> dict:
    snapshot = Path(snapshot).absolute()
    _no_links(snapshot)
    manifest_path = snapshot / 'manifest.json'
    _no_links(manifest_path)
    if not manifest_path.is_file() or (not allow_incomplete and (snapshot / INCOMPLETE).exists()):
        raise RetroBackupError('El respaldo retro está incompleto.')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise RetroBackupError('Manifiesto retro inválido.') from exc
    if (not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or
            manifest.get('status') != 'verified' or manifest.get('scope') != 'opaque_emulator_save_files' or
            manifest.get('writes_to_psp') is not False or
            manifest.get('savestate_compatibility_verified') is not False or
            not isinstance(manifest.get('files'), dict)):
        raise RetroBackupError('Esquema o estado de respaldo retro no admitido.')
    _identity(manifest.get('volume_serial'), manifest.get('usb_device_id'))
    files = manifest['files']
    if len(files) > MAX_FILES or manifest.get('file_count') != len(files):
        raise RetroBackupError('Número de archivos retro incoherente.')
    total, seen = 0, set()
    for relative, record in files.items():
        _relative(relative)
        if relative.casefold() in seen or not isinstance(record, dict):
            raise RetroBackupError('Ruta duplicada o registro retro inválido.')
        seen.add(relative.casefold())
        size, digest = record.get('size'), record.get('sha256')
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES or not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise RetroBackupError('Tamaño o SHA-256 inválido en el respaldo retro.')
        file = snapshot / 'data' / relative
        _no_links(file)
        if not file.is_file() or file.stat().st_size != size or _hash(file) != digest:
            raise RetroBackupError('Archivo retro del PC ausente, modificado o corrupto: ' + relative)
        total += size
    if total > MAX_TOTAL_BYTES or total != manifest.get('size_bytes'):
        raise RetroBackupError('Tamaño total del respaldo retro incoherente.')
    data = snapshot / 'data'
    _no_links(data)
    if not data.is_dir():
        raise RetroBackupError('Falta la carpeta de datos retro.')
    actual = set()
    count = 0
    for folder, dirs, names in os.walk(data, followlinks=False):
        for name in dirs + names:
            _no_links(Path(folder) / name)
        count += len(dirs) + len(names)
        if count > MAX_ENTRIES or len(Path(folder).relative_to(data).parts) > MAX_DEPTH + 3:
            raise RetroBackupError('El archivo PC supera los límites de inventario.')
        for name in names:
            item = Path(folder) / name
            if not item.is_file():
                raise RetroBackupError('Entrada no regular en el respaldo PC.')
            actual.add(_relative(item.relative_to(data).as_posix()))
    if actual != set(files):
        raise RetroBackupError('Archivos retro sin manifiesto o ausentes en el PC.')
    allowed = {'data', 'manifest.json'} | ({INCOMPLETE} if allow_incomplete else set())
    if {item.name for item in snapshot.iterdir()} != allowed:
        raise RetroBackupError('El respaldo contiene entradas externas no registradas.')
    return manifest


def verify_archive(snapshot: Path) -> dict:
    manifest = _verify(snapshot)
    return {'directory': str(Path(snapshot).absolute()), 'status': 'verified',
            'file_count': manifest['file_count'], 'size_bytes': manifest['size_bytes'],
            'volume_serial': manifest['volume_serial'], 'usb_device_id': manifest['usb_device_id'],
            'writes_to_psp': False, 'savestate_compatibility_verified': False}


def matching_backup(root: Path, snapshot: Path, serial: str, usb_id: str, *, same_device) -> dict:
    """Check current selected PSP bytes against an independently verified archive."""
    _identity(serial, usb_id)
    _same(same_device)
    manifest = _verify(snapshot)
    if (manifest['volume_serial'], manifest['usb_device_id']) != (serial, usb_id):
        raise RetroBackupError('El respaldo retro pertenece a otra PSP o Memory Stick.')
    current = selection(root)
    if set(current['files']) != set(manifest['files']):
        raise RetroBackupError('Los guardados retro actuales difieren del respaldo.')
    for relative, entry in current['files'].items():
        _same(same_device)
        stored = manifest['files'][relative]
        if entry['size'] != stored['size'] or _hash(_safe(Path(root).absolute(), Path(root).absolute() / relative)) != stored['sha256']:
            raise RetroBackupError('El respaldo retro no refleja el guardado actual: ' + relative)
    _same(same_device)
    return verify_archive(snapshot)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Respaldo opaco de guardados retro, sólo PSP a PC')
    parser.add_argument('--drive', help='Raíz de la PSP, por ejemplo G:\\')
    parser.add_argument('--destination', type=Path, help='Carpeta nueva absoluta en el PC')
    parser.add_argument('--run', action='store_true', help='Crear el respaldo; sin esta opción sólo se inventaría')
    parser.add_argument('--verify', type=Path, help='Verificar respaldo PC sin acceder a la PSP')
    args = parser.parse_args(argv)
    if args.verify is not None and (args.drive or args.destination or args.run):
        parser.error('--verify se usa solo, sin acceder a la PSP.')
    if args.verify is None and (not args.drive or (args.run and args.destination is None)):
        parser.error('Indica --drive; --run necesita además --destination.')
    try:
        if args.verify is not None:
            result = verify_archive(args.verify)
        else:
            if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
                raise RetroBackupError('Acceso USB real deshabilitado durante las pruebas.')
            first, _ = identify(args.drive)
            def same():
                return identify(args.drive)[0] == first
            if args.run:
                result = backup(Path(first.drive), args.destination, first.volume_serial,
                                first.usb_device_id, same_device=same)
            else:
                _same(same)
                result = {'identity': asdict(first), 'selection': selection(Path(first.drive)),
                          'copied': False, 'writes_to_psp': False}
                _same(same)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'writes_to_psp': False}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
