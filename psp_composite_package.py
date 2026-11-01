"""Build a verified PC-only test package, never install it onto a PSP.

The original PSP Dock folder, firmware flash and plugins are not touched.
This package is for a single reversible read-only hardware test on a
separate, backed-up Memory Stick. Execution remains a physical PSP action.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import uuid

from psp_composite_client import PROBE_SHA256, PROBE_SIZE, _new_local_destination


BASE = Path(__file__).resolve().parent
FIRMWARE = BASE / 'homebrew/composite-readonly'
FILES = ('EBOOT.PBP', 'dockcomposite.prx', 'PROBE.DAT', 'LICENSE-FuSa-GPL-3.0.txt')
REMOTE_FOLDER = 'PSP/GAME/PSPDockCompositeTest'


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as src:
        while data := src.read(1024 * 1024):
            value.update(data)
    return value.hexdigest()


def make_package(destination: Path, *, dist: Path = FIRMWARE / 'dist') -> dict:
    target = _new_local_destination(Path(destination))
    source = Path(dist).resolve()
    for name in (*FILES, 'dockcomposite.elf'):
        path = source / name
        if not path.is_file() or path.is_symlink() or not 1 <= path.stat().st_size <= 4 * 1024 * 1024:
            raise ValueError('Falta una compilación PSPDEV completa: ' + name)
    if ((source / 'PROBE.DAT').stat().st_size != PROBE_SIZE or
            digest(source / 'PROBE.DAT') != PROBE_SHA256):
        raise ValueError('El archivo ficticio no coincide con el manifiesto independiente.')
    spec = importlib.util.spec_from_file_location('pdc_package_elf_audit', FIRMWARE / 'audit_elf.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    abi = verifier.audit((source / 'dockcomposite.elf').read_bytes())
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ('.pspdock-package-' + uuid.uuid4().hex)
    package = stage / REMOTE_FOLDER
    package.mkdir(parents=True, exist_ok=False)
    records = {}
    for name in FILES:
        original = source / name
        expected = digest(original)
        copied = package / name
        with original.open('rb') as inp, copied.open('xb') as out:
            shutil.copyfileobj(inp, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if digest(copied) != expected or digest(original) != expected:
            raise ValueError('El archivo compilado cambió mientras se empaquetaba: ' + name)
        records[name] = {'sha256': expected, 'size': copied.stat().st_size}
    manifest = {
        'schema': 'pspdock-composite-test-package-v1',
        'built_utc': datetime.now(timezone.utc).isoformat(),
        'revision': '0203', 'folder': REMOTE_FOLDER,
        'read_only_protocol': True, 'save_return_supported': False,
        'installed_on_psp': False, 'hardware_execution_verified': False,
        'session_time_limit': None, 'compiled_abi': abi,
        'files': records,
    }
    with (stage / 'manifest.json').open('x', encoding='utf-8') as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write('\n')
    (stage / 'LEER-ANTES-DE-PROBAR.txt').write_text(
        'PSP Dock Composite TEST — solo lectura\n\n'
        'Este paquete NO ha sido instalado ni ejecutado en la PSP.\n'
        'Usar una Memory Stick de pruebas respaldada. No sustituir PSP/GAME/PSPDock.\n'
        'No modificar PLUGINS.TXT, ARK, flash ni drivers de Windows.\n'
        'El controlador mantiene HID y archivos en dos interfaces del mismo USB.\n'
        'No monta la Memory Stick como disco de Windows y no escribe partidas.\n'
        'No hay límite de tiempo; START+SELECT solicita la salida segura.\n'
        'El primer ensayo copia SOLO el PROBE.DAT ficticio y compara su SHA-256.\n'
        'Si falla la enumeración, registrar el código; no reinstalar drivers a ciegas.\n'
        'La devolución de guardados y el cierre ordenado por el host no están implementados.\n',
        encoding='utf-8',
    )
    if target.exists():
        raise FileExistsError('El destino apareció durante el empaquetado.')
    stage.rename(target)
    verify_package(target)
    return {'package': str(target), 'files': len(FILES), 'revision': '0203',
            'installed_on_psp': False, 'hardware_execution_verified': False}


def verify_package(path: Path) -> dict:
    root = Path(path)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('schema') != 'pspdock-composite-test-package-v1' or
            manifest.get('folder') != REMOTE_FOLDER or manifest.get('revision') != '0203' or
            set(manifest.get('files', {})) != set(FILES)):
        raise ValueError('No es el paquete de prueba de PSP Dock revisado.')
    directory = root / REMOTE_FOLDER
    if set(x.name for x in directory.iterdir()) != set(FILES):
        raise ValueError('El paquete contiene archivos inesperados.')
    for name, entry in manifest['files'].items():
        file = directory / name
        if (not file.is_file() or file.is_symlink() or
                file.stat().st_size != entry['size'] or digest(file) != entry['sha256']):
            raise ValueError('Integridad del paquete incorrecta: ' + name)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description='Preparar paquete de prueba SOLO en el PC')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', type=Path, metavar='NEW_PC_DIRECTORY')
    group.add_argument('--verify', type=Path, metavar='EXISTING_PC_PACKAGE')
    args = parser.parse_args()
    try:
        result = make_package(args.prepare) if args.prepare else verify_package(args.verify)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print('Paquete no preparado/verificado:', str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
