"""Build a new, audited 0301 package on the PC. Never accesses a PSP.

The four installable files target PSPDockNative only. PDK1/0207 schemas and
guards are not reused as a substitute for native-storage validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import shutil
import os
import struct
import uuid

from psp_composite_client import _new_local_destination
from psp_msc_package import ARTWORK, digest, _regular

BASE = Path(__file__).resolve().parent
FIRMWARE = BASE / 'homebrew/native-dock'
ASSETS = BASE / 'homebrew/composite-mscdemo/assets'
REMOTE_FOLDER = 'PSP/GAME/PSPDockNative'
TITLE = 'PSP Dock - Memory Stick + Mando 0301'
USB_IDENTITY = {'vid': '054C', 'pid': '01CD', 'revision': '0301'}
FILES = ('EBOOT.PBP', 'docknative.prx', 'LICENSE-FuSa-GPL-3.0.txt', 'LICENSE-DejaVu.txt')
SCHEMA = 'pspdock-native-package-v1'


def _no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('No se admiten enlaces en el paquete nativo.')


def audit_pbp(raw: bytes, *, assets: Path = ASSETS) -> dict:
    """Parse TITLE from SFO and compare every embedded PNG to approved art."""
    import hashlib
    if (not 40 <= len(raw) <= 4 * 1024 * 1024 or raw[:4] != b'\0PBP' or
            struct.unpack_from('<I', raw, 4)[0] != 0x10000):
        raise ValueError('Cabecera PBP nativa no válida.')
    offsets = struct.unpack_from('<8I', raw, 8)
    if (offsets[0] != 40 or list(offsets) != sorted(offsets) or
            offsets[-1] > len(raw) or offsets[7] - offsets[6] < 32000):
        raise ValueError('Secciones PBP nativas no válidas.')
    if raw[offsets[6]:offsets[6]+4] not in (b'\x7fELF', b'~PSP'):
        raise ValueError('Falta el ejecutable PSP dentro del paquete nativo.')
    sfo = raw[offsets[0]:offsets[1]]
    if len(sfo) < 20 or sfo[:4] != b'\0PSF':
        raise ValueError('Falta PARAM.SFO nativo.')
    _, version, keys, data, count = struct.unpack_from('<5I', sfo)
    if version != 0x101 or count > 64 or not 20 + 16*count <= keys <= data <= len(sfo):
        raise ValueError('Tabla PARAM.SFO nativa no válida.')
    title, names = None, set()
    for index in range(count):
        key_offset, kind, length, capacity, value_offset = struct.unpack_from('<HHIII', sfo, 20+16*index)
        key_start, start = keys + key_offset, data + value_offset
        key_end = sfo.find(b'\0', key_start, data)
        if key_end < key_start or length > capacity or start + capacity > len(sfo):
            raise ValueError('Entrada PARAM.SFO nativa no válida.')
        key = sfo[key_start:key_end]
        if not key or key in names:
            raise ValueError('Clave PARAM.SFO vacía o repetida.')
        names.add(key)
        if key == b'TITLE':
            if kind != 0x204 or not length or sfo[start+length-1] != 0:
                raise ValueError('TITLE no es una cadena válida.')
            title = sfo[start:start+length-1].decode('utf-8', 'strict')
    if title != TITLE:
        raise ValueError('El título no identifica PSP Dock nativo 0301.')
    artwork = {}
    for index, name, dimensions in ARTWORK:
        png = raw[offsets[index]:offsets[index+1]]
        original = Path(assets) / name
        _no_links(original)
        if (not _regular(original) or len(png) < 33 or len(png) > 300000 or
                png[:8] != b'\x89PNG\r\n\x1a\n' or png[12:16] != b'IHDR' or
                struct.unpack_from('>II', png, 16) != dimensions or png != original.read_bytes()):
            raise ValueError('El recurso XMB no coincide con el arte aprobado: ' + name)
        artwork[name] = {'sha256': hashlib.sha256(png).hexdigest(), 'size': len(png),
                         'width': dimensions[0], 'height': dimensions[1]}
    return {'title': title, 'embedded': True, 'artwork': artwork}


def _kernel_auditor():
    spec = importlib.util.spec_from_file_location('native_package_elf_audit', FIRMWARE / 'audit_elf.py')
    if spec is None or spec.loader is None:
        raise ValueError('No se encuentra la auditoría ELF nativa.')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier


def _valid_abi(abi: dict) -> bool:
    expected = {'revision': '0301', 'elf_machine': 'MIPS', 'kernel_module_verified': True,
                'mandatory_modules': ['flash0:/kd/semawm.prx', 'flash0:/kd/usbstor.prx',
                                      'flash0:/kd/usbstormgr.prx', 'flash0:/kd/usbstorms.prx'],
                'optional_bootstrap_modules': [],
                'function_descriptors_verified': True, 'hid_function_interfaces': 1,
                'hid_input_bytes': 8, 'hid_feature_bytes': 32, 'protocol': 'PDK2/v2',
                'storage_backend': 'native Sony USBStor_Driver', 'kernel_filesystem_imports': [],
                'early_sony_boot_imports': [], 'expected_pid': '01CD',
                'wire_identity_verified': False, 'sony_composition_physically_verified': False,
                'real_memory_stick_access_physically_verified': False,
                'safe_phase_is_windows_eject_proof': False}
    return (isinstance(abi, dict) and all(type(abi.get(k)) is type(v) and abi.get(k) == v for k, v in expected.items())
            and isinstance(abi.get('sha256'), str) and bool(re.fullmatch('[0-9a-f]{64}', abi['sha256'])))


def make_package(destination: Path, *, dist: Path = FIRMWARE / 'dist') -> dict:
    target = _new_local_destination(Path(destination))
    source = Path(dist)
    _no_links(source)
    for name in (*FILES, 'docknative.elf', 'compiled-audit.json'):
        file = source / name
        if not _regular(file) or not 1 <= file.stat().st_size <= 4 * 1024 * 1024:
            raise ValueError('Falta una compilación nativa completa: ' + name)
    abi = _kernel_auditor().audit((source / 'docknative.elf').read_bytes())
    if not _valid_abi(abi) or abi['sha256'] != digest(source / 'docknative.elf'):
        raise ValueError('La auditoría no corresponde al ELF nativo 0301.')
    producer = json.loads((source / 'compiled-audit.json').read_text(encoding='utf-8'))
    if (not isinstance(producer, dict) or any(producer.get(k) != v for k, v in abi.items()) or
            producer.get('pbp_sha256') != digest(source / 'EBOOT.PBP') or
            not isinstance(producer.get('user_app'), dict) or
            producer.get('user_app', {}).get('user_module_verified') is not True or
            producer.get('user_app', {}).get('privileged_usb_or_kernel_imports') != []):
        raise ValueError('El informe de compilación no acredita este ELF/PBP y su launcher USER.')
    visual = audit_pbp((source / 'EBOOT.PBP').read_bytes())
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ('.pspdock-native-package-' + uuid.uuid4().hex)
    folder = stage / REMOTE_FOLDER
    folder.mkdir(parents=True, exist_ok=False)
    records = {}
    for name in FILES:
        original, copied = source / name, folder / name
        expected = digest(original)
        with original.open('rb') as reader, copied.open('xb') as writer:
            shutil.copyfileobj(reader, writer);writer.flush();os.fsync(writer.fileno())
        if digest(copied) != expected or digest(original) != expected:
            raise ValueError('La compilación cambió durante el empaquetado: ' + name)
        records[name] = {'sha256': expected, 'size': copied.stat().st_size}
    if (digest(source / 'docknative.elf') != abi['sha256'] or
            records['EBOOT.PBP']['sha256'] != producer['pbp_sha256']):
        raise ValueError('La compilación cambió tras su auditoría.')
    manifest = {'schema': SCHEMA, 'revision': '0301', 'folder': REMOTE_FOLDER,
                'built_utc': datetime.now(timezone.utc).isoformat(),
                'expected_usb_identity': dict(USB_IDENTITY), 'synthetic_storage': False,
                'storage_backend': 'native Sony USBStor_Driver', 'storage_read_only': False,
                'real_memory_stick_access_requested': True, 'installed_on_psp': False,
                'hardware_execution_verified': False, 'compiled_abi': abi,
                'user_module_verified': True, 'xmb': visual, 'files': records}
    with (stage / 'manifest.json').open('x', encoding='utf-8') as writer:
        json.dump(manifest, writer, ensure_ascii=False, indent=2);writer.write('\n')
        writer.flush();os.fsync(writer.fileno())
    (stage / 'LEER-ANTES-DE-PROBAR.txt').write_text(
        TITLE + '\n\n'
        'Este paquete usa la Memory Stick real mediante el almacenamiento Sony.\n'
        'Incluye thumbnail ICON0, placa PIC0 y fondo PIC1 aprobados dentro de EBOOT.PBP.\n'
        'Instalar con psp_native_install.py en una carpeta PSP/GAME/PSPDockNative NUEVA.\n'
        'Para actualizar la 0300 revisada, usar psp_native_upgrade.py con backup de la app en el PC y recibo nuevo.\n'
        'El instalador exige la PSP identificada en modo USB nativo XMB y un backup actual.\n'
        'Abrir PSP Dock en Windows; salir de Conexion USB en PSP y quitar el cable.\n'
        'Abrir esta app en PSP, pulsar X y conectar cuando aparezca LISTO.\n'
        'Windows sincroniza/inventaría, permite elegir partidas/juegos/todo/nada y activa el mando.\n'
        'Las copias bloquean el mando; al salir se devuelven sólo partidas y datos de emuladores compatibles; los estados sin formato verificado se conservan en el PC.\n'
        'Esperar la sincronización y expulsión en Windows antes de retirar el cable.\n'
        'START+SELECT solicita salida; nunca corta el almacenamiento con el cable conectado.\n'
        'La composición Sony USBSTOR + HID y su identidad exacta requieren prueba física.\n', encoding='utf-8')
    verify_package(stage)
    if target.exists():
        raise FileExistsError('El destino apareció durante el empaquetado.')
    stage.rename(target)
    verify_package(target)
    return {'package': str(target), 'revision': '0301', 'files': len(FILES), 'xmb': visual,
            'installed_on_psp': False, 'hardware_execution_verified': False}


def verify_package(path: Path) -> dict:
    root = Path(path)
    _no_links(root)
    manifest_file = root / 'manifest.json'
    if not _regular(manifest_file) or manifest_file.stat().st_size > 256000:
        raise ValueError('Manifiesto nativo ausente o no válido.')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or
            manifest.get('revision') != '0301' or manifest.get('folder') != REMOTE_FOLDER or
            manifest.get('expected_usb_identity') != USB_IDENTITY or
            manifest.get('synthetic_storage') is not False or
            manifest.get('storage_backend') != 'native Sony USBStor_Driver' or
            manifest.get('storage_read_only') is not False or
            manifest.get('real_memory_stick_access_requested') is not True or
            manifest.get('hardware_execution_verified') is not False or
            manifest.get('user_module_verified') is not True or
            not _valid_abi(manifest.get('compiled_abi')) or
            not isinstance(manifest.get('files'), dict) or set(manifest['files']) != set(FILES)):
        raise ValueError('No es el paquete nativo 0301 revisado.')
    folder = root / REMOTE_FOLDER
    _no_links(folder)
    if not folder.is_dir() or {p.name for p in folder.iterdir()} != set(FILES):
        raise ValueError('El paquete nativo no contiene exactamente los cuatro archivos.')
    for name, record in manifest['files'].items():
        file = folder / name
        if (not isinstance(record, dict) or not _regular(file) or
                type(record.get('size')) is not int or not 1 <= record['size'] <= 4*1024*1024 or
                not isinstance(record.get('sha256'), str) or
                not re.fullmatch('[0-9a-f]{64}', record['sha256']) or
                file.stat().st_size != record['size'] or digest(file) != record['sha256']):
            raise ValueError('Integridad nativa incorrecta: ' + name)
    visual = audit_pbp((folder / 'EBOOT.PBP').read_bytes())
    if visual != manifest.get('xmb'):
        raise ValueError('Los recursos XMB no coinciden con el manifiesto nativo.')
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Preparar PSP Dock nativo 0301 con sus recursos XMB')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', type=Path, metavar='NEW_PC_DIRECTORY')
    group.add_argument('--verify', type=Path, metavar='EXISTING_PC_PACKAGE')
    parser.add_argument('--dist', type=Path, default=FIRMWARE / 'dist')
    args = parser.parse_args(argv)
    try:
        result = make_package(args.prepare, dist=args.dist) if args.prepare else verify_package(args.verify)
        print(json.dumps(result, ensure_ascii=False, indent=2));return 0
    except (OSError, ValueError) as exc:
        print('Paquete nativo no preparado/verificado:', str(exc));return 1


if __name__ == '__main__':
    raise SystemExit(main())
