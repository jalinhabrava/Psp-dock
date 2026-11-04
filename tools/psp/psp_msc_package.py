"""Package the synthetic USBSTOR + HID test, including its XMB artwork.

This creates a new PC package. The only PSP installation target is the
separate PSPDockStorageTest folder, through psp_msc_install.py.
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
import struct
import uuid

from psp_dock.devices.psp_composite_client import _new_local_destination

BASE = Path(__file__).resolve().parents[2]
FIRMWARE = BASE / 'homebrew/composite-mscdemo'
REMOTE_FOLDER = 'PSP/GAME/PSPDockStorageTest'
TITLE = 'PSP Dock - USB Storage + HID 0207'
USB_IDENTITY = {'vid': '054C', 'pid': '01CC', 'revision': '0207'}
FILES = ('EBOOT.PBP', 'dockmsc.prx', 'LICENSE-FuSa-GPL-3.0.txt', 'LICENSE-DejaVu.txt')
ARTWORK = ((1, 'ICON0.PNG', (144, 80)), (3, 'PIC0.PNG', (310, 180)),
           (4, 'PIC1.PNG', (480, 272)))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as reader:
        while block := reader.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and not (
        getattr(path.stat(), 'st_file_attributes', 0) & 0x400)


def audit_pbp(raw: bytes, *, assets: Path | None = None) -> dict:
    if (not 40 <= len(raw) <= 4 * 1024 * 1024 or raw[:4] != b'\0PBP' or
            struct.unpack_from('<I', raw, 4)[0] != 0x00010000):
        raise ValueError('El EBOOT no tiene una cabecera PBP valida.')
    offsets = struct.unpack_from('<8I', raw, 8)
    if (offsets[0] != 40 or list(offsets) != sorted(offsets) or
            offsets[-1] > len(raw) or offsets[7] - offsets[6] < 32_000):
        raise ValueError('Las secciones del EBOOT no son validas.')
    executable = raw[offsets[6]:offsets[7]]
    if executable[:4] not in (b'\x7fELF', b'~PSP'):
        raise ValueError('Falta el ejecutable PSP dentro del EBOOT.')
    sfo = raw[offsets[0]:offsets[1]]
    if len(sfo) < 20 or sfo[:4] != b'\0PSF':
        raise ValueError('Falta PARAM.SFO.')
    _, version, keys, data, count = struct.unpack_from('<5I', sfo)
    if (version != 0x00000101 or count > 64 or
            not 20 + 16 * count <= keys <= data <= len(sfo)):
        raise ValueError('PARAM.SFO no es valido.')
    title = None
    names = set()
    for i in range(count):
        key_offset, kind, length, capacity, value_offset = struct.unpack_from('<HHIII', sfo, 20 + 16 * i)
        key_start = keys + key_offset
        key_end = sfo.find(b'\0', key_start, data)
        start = data + value_offset
        if key_end < key_start or length > capacity or start + capacity > len(sfo):
            raise ValueError('Entrada PARAM.SFO no valida.')
        key = sfo[key_start:key_end]
        if not key or key in names:
            raise ValueError('PARAM.SFO contiene una clave vacia o repetida.')
        names.add(key)
        if key == b'TITLE':
            if kind != 0x0204 or not length or sfo[start + length - 1] != 0:
                raise ValueError('TITLE no es una cadena PARAM.SFO valida.')
            title = sfo[start:start + length].rstrip(b'\0').decode('utf-8', 'strict')
    if title != TITLE:
        raise ValueError('El titulo XMB no identifica la prueba Storage + HID.')
    artwork = {}
    for index, name, expected in ARTWORK:
        png = raw[offsets[index]:offsets[index + 1]]
        if (len(png) < 33 or len(png) > 300_000 or
                png[:8] != b'\x89PNG\r\n\x1a\n' or png[12:16] != b'IHDR' or
                struct.unpack_from('>II', png, 16) != expected):
            raise ValueError('Falta el recurso XMB o tiene otras dimensiones: ' + name)
        if assets is not None and png != (Path(assets) / name).read_bytes():
            raise ValueError('El recurso XMB incrustado no coincide con el original: ' + name)
        artwork[name] = {'sha256': hashlib.sha256(png).hexdigest(),
                         'size': len(png), 'width': expected[0], 'height': expected[1]}
    return {'title': title, 'embedded': True, 'artwork': artwork}


def make_package(destination: Path, *, dist: Path = FIRMWARE / 'dist') -> dict:
    target = _new_local_destination(Path(destination))
    source = Path(dist).resolve()
    for name in (*FILES, 'dockmsc.elf'):
        path = source / name
        if not _regular(path) or not 1 <= path.stat().st_size <= 4 * 1024 * 1024:
            raise ValueError('Falta una compilacion PSP completa: ' + name)
    spec = importlib.util.spec_from_file_location('msc_package_elf_audit', FIRMWARE / 'audit_elf.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    abi = verifier.audit((source / 'dockmsc.elf').read_bytes())
    visual = audit_pbp((source / 'EBOOT.PBP').read_bytes(), assets=FIRMWARE / 'assets')
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ('.pspdock-msc-package-' + uuid.uuid4().hex)
    package = stage / REMOTE_FOLDER
    package.mkdir(parents=True, exist_ok=False)
    records = {}
    for name in FILES:
        original, copied = source / name, package / name
        expected = digest(original)
        with original.open('rb') as reader, copied.open('xb') as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if digest(copied) != expected or digest(original) != expected:
            raise ValueError('La compilacion cambio durante el empaquetado: ' + name)
        records[name] = {'sha256': expected, 'size': copied.stat().st_size}
    manifest = {
        'schema': 'pspdock-msc-test-package-v1',
        'built_utc': datetime.now(timezone.utc).isoformat(),
        'revision': '0207', 'folder': REMOTE_FOLDER,
        'expected_usb_identity': dict(USB_IDENTITY),
        'synthetic_storage': True, 'real_memory_stick_access': False,
        'storage_read_only': True, 'installed_on_psp': False,
        'hardware_execution_verified': False, 'compiled_abi': abi,
        'xmb': visual, 'files': records,
    }
    (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (stage / 'LEER-ANTES-DE-PROBAR.txt').write_text(
        TITLE + '\n\n'
        'Incluye ICON0 (thumbnail), PIC0 (previsualizacion) y PIC1 (fondo) en EBOOT.PBP.\n'
        'La unidad USB de 512 KiB es FICTICIA y solo admite lecturas.\n'
        'Contiene PROBE.DAT y SESSION.BIN generados en memoria.\n'
        'No ofrece acceso a juegos ni partidas reales de la Memory Stick.\n'
        'Instalar solamente con psp_msc_install.py en PSP/GAME/PSPDockStorageTest.\n'
        'Para ejecutar: salir de Conexion USB, quitar cable, abrir esta prueba y pulsar X.\n'
        'Cuando aparezca LISTO, conectar cable y mover la cruceta mientras lee Windows.\n'
        'START+SELECT durante 1 segundo solicita cierre y espera a completar callbacks.\n'
        'El monitor PC registra USBSTOR, HID, hashes y movimiento durante las lecturas.\n'
        'La compilacion por si sola no confirma que ambas interfaces funcionen en PSP.\n', encoding='utf-8')
    if target.exists():
        raise FileExistsError('El destino aparecio durante el empaquetado.')
    stage.rename(target)
    verify_package(target)
    return {'package': str(target), 'revision': '0207', 'files': len(FILES),
            'xmb': visual, 'installed_on_psp': False, 'hardware_execution_verified': False}


def verify_package(path: Path) -> dict:
    root = Path(path)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('schema') != 'pspdock-msc-test-package-v1' or
            manifest.get('revision') != '0207' or manifest.get('folder') != REMOTE_FOLDER or
            manifest.get('expected_usb_identity') != USB_IDENTITY or
            manifest.get('synthetic_storage') is not True or
            manifest.get('real_memory_stick_access') is not False or
            manifest.get('storage_read_only') is not True or
            set(manifest.get('files', {})) != set(FILES)):
        raise ValueError('No es el paquete sintetico MSC + HID revisado.')
    directory = root / REMOTE_FOLDER
    for parent in (directory, *directory.parents):
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ValueError('No se admiten enlaces en el paquete.')
    if set(item.name for item in directory.iterdir()) != set(FILES):
        raise ValueError('El paquete contiene archivos inesperados.')
    for name, entry in manifest['files'].items():
        file = directory / name
        if (not _regular(file) or type(entry.get('size')) is not int or
                not 1 <= entry['size'] <= 4 * 1024 * 1024 or
                file.stat().st_size != entry['size'] or digest(file) != entry.get('sha256')):
            raise ValueError('Integridad del paquete incorrecta: ' + name)
    visual = audit_pbp((directory / 'EBOOT.PBP').read_bytes())
    if visual != manifest.get('xmb'):
        raise ValueError('Los recursos XMB no coinciden con el manifiesto.')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description='Preparar el ensayo Storage + HID con recursos XMB')
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
