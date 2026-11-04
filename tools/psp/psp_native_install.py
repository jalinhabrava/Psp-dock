"""Install the final audited PSP Dock 0308 into a new folder from Sony XMB USB.

No upgrade, firmware execution, USB transition, flash, plugins or drivers.
Every installation has a persistent PC receipt/journal and a current backup.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from psp_dock.devices.psp_composite_client import _new_local_destination
from tools.psp.native_backup import no_links, matching_backup
from tools.psp.psp_native_package import FILES, REMOTE_FOLDER, digest, verify_package
from psp_dock.devices.psp_usb import identify, UsbTransitionError

XMB_USB = re.compile(r'^USB\\VID_054C&PID_02D2\\[^\\]+$', re.I)


def _check_identity(serial: str, usb_id: str) -> None:
    if (not isinstance(serial, str) or not serial.isdigit() or
            not isinstance(usb_id, str) or not XMB_USB.fullmatch(usb_id)):
        raise ValueError('La instalación requiere la PSP comprobada en USB nativo XMB 054C:02D2.')


def _on_pc(path: Path, root: Path) -> Path:
    result = _new_local_destination(Path(path))
    if result.is_relative_to(root.resolve()):
        raise ValueError('El recibo y su journal deben estar en el PC, no en la PSP.')
    return result


def _write_new_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as writer:
        json.dump(value, writer, ensure_ascii=False, indent=2);writer.write('\n')
        writer.flush();os.fsync(writer.fileno())


def plan_install(package: Path, root: Path, snapshot: Path, serial: str, usb_id: str) -> dict:
    _check_identity(serial, usb_id)
    package, root, snapshot = map(Path, (package, root, snapshot))
    for path in (package, root, snapshot, root / 'PSP/GAME'):
        no_links(path)
    manifest = verify_package(package)
    if not (root / 'PSP/GAME').is_dir():
        raise ValueError('No existe PSP/GAME en la PSP identificada.')
    target = root / REMOTE_FOLDER
    no_links(target)
    if target.exists():
        raise FileExistsError('Ya existe PSPDockNative; este instalador no sobrescribe ni actualiza.')
    matching_backup(root, snapshot, serial, usb_id)
    size = sum(record['size'] for record in manifest['files'].values())
    if shutil.disk_usage(root).free < size + 8 * 1024 * 1024:
        raise ValueError('Falta espacio para PSP Dock nativo.')
    return {'schema': 'pspdock-native-install-receipt-v1', 'revision': manifest['revision'],
            'target': str(target), 'backup': str(snapshot), 'files': manifest['files'],
            'xmb': manifest['xmb'], 'bytes': size, 'synthetic_storage': False,
            'storage_backend': 'native Sony USBStor_Driver',
            'real_memory_stick_access_requested': True, 'installed': False,
            'hardware_execution_verified': False, 'autorun': False,
            'filesystem_writes_only': REMOTE_FOLDER,
            'identity': {'volume_serial': serial, 'usb_device_id': usb_id}}


def install_package(package: Path, root: Path, snapshot: Path, serial: str, usb_id: str,
                    *, same_device, receipt: Path, identity: dict | None = None) -> dict:
    if not same_device():
        raise ValueError('Cambió la PSP antes de preparar la instalación nativa.')
    plan = plan_install(package, root, snapshot, serial, usb_id)
    root, package = Path(root), Path(package)
    if identity is not None:
        if (not isinstance(identity, dict) or identity.get('volume_serial') != serial or
                identity.get('usb_device_id') != usb_id):
            raise ValueError('La identidad del recibo no coincide con esta PSP.')
        plan['identity'] = dict(identity)
    receipt = _on_pc(Path(receipt), root)
    journal = _on_pc(receipt.with_name(receipt.name + '.journal.jsonl'), root)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    target = Path(plan['target'])
    staging = target.parent / ('.pspdock-native-' + uuid.uuid4().hex)
    plan.update(receipt=str(receipt), journal=str(journal), staging=str(staging))
    _write_new_json(receipt, plan)

    def note(phase: str, **facts) -> None:
        no_links(journal)
        with journal.open('a', encoding='utf-8') as writer:
            json.dump({'phase': phase, 'utc': datetime.now(timezone.utc).isoformat(), **facts},
                      writer, ensure_ascii=False)
            writer.write('\n');writer.flush();os.fsync(writer.fileno())

    # Reserve the journal exclusively; never append to another installation.
    with journal.open('x', encoding='utf-8') as writer:
        writer.flush();os.fsync(writer.fileno())
    note('planned', target=str(target), files=plan['files'], identity=plan['identity'])
    try:
        if not same_device():
            raise ValueError('Cambió la PSP antes de crear la carpeta provisional.')
        staging.mkdir(exist_ok=False)
        note('staging_created')
        # No cleanup after failure: a disconnected drive letter may now refer
        # to another device. Only these four new files are ever written.
        for name in FILES:
            if not same_device():
                raise ValueError('Cambió la PSP durante la copia; se conserva el parcial.')
            source, copied = package / REMOTE_FOLDER / name, staging / name
            no_links(source);no_links(staging)
            expected = plan['files'][name]
            with source.open('rb') as reader, copied.open('xb') as writer:
                shutil.copyfileobj(reader, writer);writer.flush();os.fsync(writer.fileno())
            if not same_device():
                raise ValueError('Cambió la PSP antes de verificar la copia; no se publica.')
            if copied.stat().st_size != expected['size'] or digest(copied) != expected['sha256']:
                raise ValueError('La copia PSP no coincide con el paquete nativo: ' + name)
            note('file_verified', file=name, sha256=expected['sha256'])
        verify_package(package)
        if not same_device():
            raise ValueError('Cambió la PSP antes de comprobar el backup actual.')
        matching_backup(root, Path(snapshot), serial, usb_id)
        if not same_device():
            raise ValueError('Cambió la PSP antes de publicar la aplicación.')
        if target.exists():
            raise FileExistsError('Apareció PSPDockNative; no se sobrescribe.')
        if {p.name for p in staging.iterdir()} != set(FILES):
            raise ValueError('La carpeta provisional contiene archivos inesperados.')
        staging.rename(target)
        note('published')
        if not same_device():
            raise ValueError('Cambió la PSP después de publicar; comprobar antes de ejecutar.')
        if {p.name for p in target.iterdir()} != set(FILES):
            raise ValueError('La instalación contiene archivos inesperados.')
        for name in FILES:
            if not same_device():
                raise ValueError('Cambió la PSP durante la verificación final.')
            copied = target / name;no_links(copied)
            if copied.stat().st_size != plan['files'][name]['size'] or digest(copied) != plan['files'][name]['sha256']:
                raise ValueError('Falló la relectura de la instalación nativa: ' + name)
        if not same_device():
            raise ValueError('Cambió la PSP al finalizar la verificación.')
        result = {**plan, 'installed': True, 'verified_utc': datetime.now(timezone.utc).isoformat()}
        note('verified', result=result)
        # Replace only our own initial PC receipt, never a PSP file. Journal
        # already records publication if saving the final receipt fails.
        if json.loads(receipt.read_text(encoding='utf-8')) != plan:
            raise ValueError('El recibo del PC cambió; se conserva la evidencia y la instalación.')
        ready = receipt.with_name(receipt.name + '.' + uuid.uuid4().hex + '.ready')
        _write_new_json(ready, result);ready.replace(receipt)
        return result
    except Exception as exc:
        try:
            note('incomplete', error=str(exc), target=str(target), staging=str(staging))
        except OSError:
            pass  # The already-fsynced plan/previous journal remains evidence.
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Preparar o instalar PSP Dock nativo en una carpeta nueva')
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--drive', required=True)
    parser.add_argument('--install', action='store_true', help='Realizar la copia verificada; por defecto sólo plan')
    parser.add_argument('--receipt', type=Path, help='Archivo NUEVO del PC, obligatorio al instalar')
    args = parser.parse_args(argv)
    try:
        if os.name != 'nt' or os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            raise ValueError('El instalador físico requiere Windows fuera de las pruebas.')
        if args.install and args.receipt is None:
            raise ValueError('--install requiere --receipt en el PC.')
        device, _ = identify(args.drive)
        _check_identity(device.volume_serial, device.usb_device_id)
        params = (args.package, Path(device.drive), args.backup, device.volume_serial, device.usb_device_id)
        if args.install:
            result = install_package(*params, same_device=lambda: identify(device.drive)[0] == device,
                                     receipt=args.receipt, identity=asdict(device))
        else:
            result = plan_install(*params)
            result['identity'] = asdict(device)
            if args.receipt:
                target = _on_pc(args.receipt, Path(device.drive))
                target.parent.mkdir(parents=True, exist_ok=True);_write_new_json(target, result)
        print(json.dumps(result, ensure_ascii=False, indent=2));return 0
    except (OSError, ValueError, UsbTransitionError) as exc:
        print('PSP Dock nativo no instalado/verificado:', str(exc));return 1


if __name__ == '__main__':
    raise SystemExit(main())
