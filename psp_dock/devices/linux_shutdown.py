"""Durable Linux unmount followed by the same 0308 terminal USB command.

The receipt proves an unmount, never Windows media ejection or USB power-off.
No filesystem is mounted and no save operation is replayed during recovery.
"""
from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import re
import subprocess

from psp_dock.devices.native_dock import (
    NativeDockError, CAP_HOST_SHUTDOWN, SAFE, HOST_SHUTDOWN,
    _validate_identity, encode_command,
)
from psp_dock.devices.native_shutdown import NativeShutdown, _atomic_json, _STAGES
from psp_dock.devices.native_storage_release import StorageReleaseError
from psp_dock.devices.psp_usb import PspStorage

SCHEMA = 'pspdock-linux-native-shutdown-v1'
RELEASE_SCHEMA = 'pspdock-linux-unmount-v1'


def durable_json(path, value):
    _atomic_json(path, value)
    fd = os.open(str(Path(path).parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class LinuxShutdown(NativeShutdown):
    schema = SCHEMA
    eject_kind = 'unmount'

    def __init__(self, path, *, system, api_factory, **options):
        self.system = system
        options.setdefault('release', self.release_storage)
        options.setdefault('verify_release', self.verify_unmounted)
        options.setdefault('removed', self.device_removed)
        options.setdefault('persist', durable_json)
        super().__init__(path, api_factory=api_factory,
            hid_identity=system.hid_identity,
            release_verified=lambda check: check.get('unmounted') is True and check.get('same_device') is True,
            **options)

    def _validate_record(self, value):
        if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('stage') not in _STAGES:
            raise ValueError('Registro Linux de cierre desconocido.')
        current = value['observation']
        storage = PspStorage(**value['storage'])
        if not isinstance(current, dict) or type(value.get('cancel')) is not bool:
            raise ValueError('Identidad de cierre incompleta.')
        _validate_identity(current['protocol_version'], current['firmware_revision'],
                           current['capabilities'], current['build_id'])
        dev, sysfs = self.system.dev.absolute(), self.system.sysfs.absolute()
        path, parent, disk = Path(current['path']), Path(current['usb_parent']), Path(storage.disk_id)
        if (not current['capabilities'] & CAP_HOST_SHUTDOWN or path.parent != dev or
                re.fullmatch(r'hidraw[0-9]+', path.name) is None or
                not parent.is_absolute() or not parent.is_relative_to(sysfs) or
                not disk.is_relative_to(parent) or storage.usb_device_id != str(parent) or
                storage.model != 'Sony PSP Dock' or not storage.volume_serial.isdigit() or
                not 0 <= int(storage.volume_serial) <= 0xffffffff or
                not Path(storage.drive).is_absolute() or storage.drive == '/' or
                current['root'] != storage.drive or current['volume_serial'] != storage.volume_serial or
                re.fullmatch('[0-9a-f]{32}', current['session_id']) is None or
                not any(bytes.fromhex(current['session_id']))):
            raise ValueError('La identidad Linux del cierre no es válida.')
        expected = encode_command(current['session_id'], value['sequence'], SAFE, HOST_SHUTDOWN,
            firmware_revision=current['firmware_revision'], build_id=current['build_id'])
        if value['command_hex'] != expected.hex() or type(value.get('attempts')) is not int or value['attempts'] < 0:
            raise ValueError('La orden terminal no corresponde a la sesión.')
        receipt = value.get('release')
        if receipt is not None:
            self.validate_receipt(receipt, storage=value['storage'], parent=current['usb_parent'])
        if value['stage'] in ('media_released', 'sending', 'waiting_removal', 'complete'):
            self.validate_receipt(receipt, storage=value['storage'], parent=current['usb_parent'], require_released=True)

    def validate_receipt(self, receipt, *, storage=None, parent=None, require_released=False):
        if (not isinstance(receipt, dict) or receipt.get('schema') != RELEASE_SCHEMA or
                receipt.get('status') not in ('prepared', 'eject_requested', 'release_failed', 'unmounted') or
                not isinstance(receipt.get('identity'), dict) or
                not re.fullmatch(r'[0-9]+:[0-9]+', receipt.get('block_number', '')) or
                type(receipt.get('parent_inode')) is not int or receipt['parent_inode'] <= 0):
            raise ValueError('Recibo Linux de desmontaje inválido.')
        identity = PspStorage(**receipt['identity'])
        block = Path(receipt.get('block_device', ''))
        if (block.parent != self.system.dev.absolute() or
                re.fullmatch(r'[A-Za-z0-9_-]+', block.name) is None or
                receipt.get('usb_parent') != identity.usb_device_id or
                storage is not None and receipt['identity'] != storage or
                parent is not None and receipt['usb_parent'] != parent):
            raise ValueError('El recibo pertenece a otro disco o dispositivo.')
        if require_released and (receipt.get('host_eject_accepted') is not True or
                receipt.get('storage_released') is not True or receipt['status'] != 'unmounted' or
                receipt.get('release_check', {}).get('unmounted') is not True or
                receipt.get('release_check', {}).get('same_device') is not True):
            raise ValueError('El recibo no acredita un desmontaje completo.')

    def _release_proven(self):
        if self.record is None:
            return False
        try:
            self.validate_receipt(self.record.get('release'), storage=self.record['storage'],
                                  parent=self.record['observation']['usb_parent'], require_released=True)
            return True
        except (ValueError, TypeError, KeyError):
            return False

    def verify_unmounted(self, receipt):
        self.validate_receipt(receipt)
        identity = PspStorage(**receipt['identity'])
        parent = (receipt['parent_inode'], receipt['usb_parent'])
        node = self.system.sysfs / 'dev/block' / receipt['block_number']
        if (str(node.resolve(strict=True)) != identity.disk_id or
                self.system.parent_of(node) != parent):
            raise NativeDockError('El disco cambió después del desmontaje.')
        # UUID is read from udev's association; never reopen FAT during exit.
        serials = {str(int(link.name.replace('-', ''), 16))
                   for link in (self.system.dev / 'disk/by-uuid').glob('*')
                   if re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', link.name)
                   and link.is_symlink() and link.resolve() == Path(receipt['block_device'])}
        if serials != {identity.volume_serial}:
            raise NativeDockError('La identidad FAT cambió durante el cierre.')
        mounted = self.system.mounts()
        if (any(m.number == receipt['block_number'] or m.point == identity.drive for m in mounted)
                or self.system.mounts_for_parent(parent)):
            raise NativeDockError('La Memory Stick sigue montada; no se corta la conexión USB.')
        return {'unmounted': True, 'same_device': True, 'block_number': receipt['block_number'],
                'volume_serial': identity.volume_serial, 'method': 'linux_mountinfo_sysfs_uuid'}

    def release_storage(self, identity, parent, *, checkpoint):
        checked, _ = self.system.identify(identity.drive)
        mounts = self.system.mounts_for_parent(parent)
        if checked != identity or self.system.parent_of(identity.disk_id) != parent:
            raise NativeDockError('La Memory Stick cambió antes del desmontaje.')
        if len(mounts) != 1 or mounts[0].point != identity.drive:
            raise NativeDockError('Cierra otros montajes de la misma PSP antes de salir.')
        device = self.system.block_device(mounts[0])
        receipt = {'schema': RELEASE_SCHEMA, 'identity': asdict(identity),
                   'usb_parent': parent[1], 'parent_inode': parent[0],
                   'block_device': str(device), 'block_number': mounts[0].number,
                   'status': 'eject_requested', 'host_eject_accepted': False,
                   'storage_released': False}
        checkpoint(receipt)  # Persist intent before admitting the OS mutation.
        try:
            result = self.system.run(['udisksctl', 'unmount', '--block-device', str(device),
                                      '--no-user-interaction'], capture_output=True, text=True,
                                     timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise StorageReleaseError('No se confirmó el desmontaje; se conserva el recibo para comprobarlo.', receipt) from exc
        if result.returncode != 0:
            receipt['status'] = 'release_failed'
            checkpoint(receipt)
            raise StorageReleaseError('UDisks2 rechazó el desmontaje; cierra los archivos abiertos.', receipt)
        check = self.verify_unmounted(receipt)
        receipt.update(status='unmounted', host_eject_accepted=True, storage_released=True, release_check=check)
        checkpoint(receipt)
        return receipt

    def _finish(self, *, parent_of):
        # A crash/timeout after UDisks may leave an uncertain receipt. Observe
        # the same live device and kernel mount state before any retry. A new
        # unmount or terminal command cannot be inferred from missing files.
        if self.record and (self.record.get('release') or {}).get('status') == 'eject_requested':
            current = self.record['observation']
            api = self.api_factory()
            with api.open(current['path']) as handle:
                self._identity(api.get(handle), parent_of)
            receipt = dict(self.record['release'])
            check = self.verify_unmounted(receipt)
            receipt.update(status='unmounted', host_eject_accepted=True, storage_released=True, release_check=check)
            self._save(stage='media_released', release=receipt)
        return super()._finish(parent_of=parent_of)

    def device_removed(self, parent, path):
        self.system.check_access()
        if (Path(path).parent != self.system.dev or
                not Path(parent).is_relative_to(self.system.sysfs)):
            raise NativeDockError('La retirada USB no pertenece al dispositivo verificado.')
        # lstat distinguishes a genuinely absent node from permission/I/O
        # failures. An unchanged pathname with a new session is not removal.
        for node in (Path(path), Path(parent)):
            try:
                node.lstat()
            except FileNotFoundError:
                continue
            return False
        return True
