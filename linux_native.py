"""Linux PDK2 adapter: hidraw feature reports, sysfs identity and UDisks2.

The kernel HID and mass-storage drivers remain attached. Discovery never
mounts a disk, detaches a driver or changes phase. Only the final explicit
Dock exit unmounts the one already-mounted, session-bound Memory Stick.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess

from native_dock import (PID, REVISION, VID, MAGIC, NativeDockError,
                         NativeDockTransport, decode_status, probe_native,
                         request_phase)
from psp_usb import PspStorage

# The native driver includes homebrew/bridge-hid/report_descriptor.h.
# Exact matching also rejects unrelated collections sharing the Sony VID.
REPORT_DESCRIPTOR = bytes.fromhex(
    '05 01 09 04 a1 01 09 01 a1 00 09 30 09 31 09 32 09 35 15 00 26 ff 00 35 '
    '00 46 ff 00 66 00 00 75 08 95 04 81 02 c0 09 39 15 01 25 08 35 00 46 3b '
    '01 46 3b 01 65 14 75 04 95 01 81 02 05 09 19 01 29 0c 15 00 25 01 75 01 '
    '95 0c 81 02 95 10 81 03 05 08 09 43 15 00 26 ff 00 35 00 46 ff 00 75 08 '
    '95 01 91 82 09 44 91 82 09 45 91 82 09 46 91 82 06 00 ff 09 01 15 00 26 '
    'ff 00 35 00 46 ff 00 65 00 75 08 95 20 b1 02 c0')


def unescape_mount(value: str) -> str:
    return re.sub(r'\\(040|011|012|134)', lambda m: chr(int(m[1], 8)), value)


@dataclass(frozen=True)
class Mount:
    number: str
    root: str
    point: str
    options: frozenset[str]
    filesystem: str
    source: str


def parse_mountinfo(text: str) -> list[Mount]:
    result = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        fields, extra = left.split(), right.split()
        if not sep or len(fields) < 6 or len(extra) < 3 or not re.fullmatch(r'\d+:\d+', fields[2]):
            raise NativeDockError('La tabla de montajes Linux no se puede verificar.')
        result.append(Mount(fields[2], unescape_mount(fields[3]),
                            unescape_mount(fields[4]), frozenset(fields[5].split(',')),
                            extra[0], unescape_mount(extra[1])))
    return result


class LinuxSystem:
    def __init__(self, *, sysfs_root=Path('/sys'), dev_root=Path('/dev'),
                 mountinfo=Path('/proc/self/mountinfo'), run=subprocess.run):
        self.sysfs = Path(sysfs_root)
        self.dev = Path(dev_root)
        self.mountinfo = Path(mountinfo)
        self.run = run

    def check_access(self):
        if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1' and (
                self.sysfs == Path('/sys') or self.dev == Path('/dev') or
                self.mountinfo == Path('/proc/self/mountinfo')):
            raise NativeDockError('USB físico deshabilitado para las pruebas.')

    def mounts(self) -> list[Mount]:
        self.check_access()
        return parse_mountinfo(self.mountinfo.read_text(encoding='utf-8'))

    def parent_of(self, node) -> tuple[int, str]:
        self.check_access()
        resolved = Path(node).resolve(strict=True)
        if not resolved.is_relative_to(self.sysfs.resolve()):
            raise NativeDockError('La identidad no pertenece a sysfs.')
        for candidate in (resolved, *resolved.parents):
            if (candidate / 'idVendor').is_file():
                try:
                    actual = tuple(int((candidate / name).read_text().strip(), 16)
                                   for name in ('idVendor', 'idProduct', 'bcdDevice'))
                except (ValueError, OSError) as exc:
                    raise NativeDockError('No se puede leer la identidad USB Linux.') from exc
                if actual != (VID, PID, REVISION):
                    raise NativeDockError('Linux requiere PSP Dock nativo 054c:01cd revisión 0301.')
                return candidate.stat().st_ino, str(candidate)
        raise NativeDockError('El dispositivo no tiene un padre USB PSP Dock verificable.')

    def block_node(self, mount: Mount) -> Path:
        node = (self.sysfs / 'dev/block' / mount.number).resolve(strict=True)
        self.parent_of(node)
        return node

    def block_device(self, mount: Mount) -> Path:
        node = self.block_node(mount)
        values = dict(line.split('=', 1) for line in (node / 'uevent').read_text().splitlines() if '=' in line)
        name = values.get('DEVNAME', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise NativeDockError('No se puede identificar el nodo de bloque de la PSP.')
        device = self.dev / name
        if device.is_symlink():
            raise NativeDockError('El nodo de bloque de la PSP cambió.')
        return device

    def matching_mount(self, root) -> Mount:
        point = str(Path(root).absolute())
        if point == '/' or str(Path(root).resolve()) != point:
            raise NativeDockError('Selecciona la raíz montada de la Memory Stick, sin enlaces.')
        matches = [m for m in self.mounts() if m.point == point]
        if len(matches) != 1:
            raise NativeDockError('La Memory Stick debe tener un único montaje verificable.')
        mount = matches[0]
        if mount.root != '/' or mount.filesystem not in ('vfat', 'msdos') or 'rw' not in mount.options:
            raise NativeDockError('La Memory Stick debe estar montada completa, FAT y con escritura.')
        return mount

    def serial_of(self, root) -> str:
        mount = self.matching_mount(root)
        block = self.block_node(mount)
        matches = []
        for link in (self.dev / 'disk/by-uuid').glob('*'):
            if not re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', link.name) or not link.is_symlink():
                continue
            target = link.resolve()
            # Resolve udev's UUID symlink against the kernel DEVNAME, not a
            # caller-supplied /dev path from the mount table.
            if target == self.block_device(mount):
                matches.append(str(int(link.name.replace('-', ''), 16)))
        if len(set(matches)) != 1:
            raise NativeDockError('No se puede verificar el número de serie FAT de la Memory Stick.')
        # Ensure unplug/replug did not substitute the sysfs block node.
        if self.block_node(mount) != block:
            raise NativeDockError('El disco cambió durante la identificación.')
        return matches[0]

    def identify(self, root) -> tuple[PspStorage, int]:
        mount = self.matching_mount(root)
        block = self.block_node(mount)
        parent = self.parent_of(block)
        if not (Path(mount.point) / 'PSP').is_dir():
            raise NativeDockError('No hay una carpeta PSP en la Memory Stick montada.')
        identity = PspStorage(mount.point, self.serial_of(root), str(block), parent[1], 'Sony PSP Dock 0301')
        return identity, parent[0]

    def devices(self) -> list[dict]:
        if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1' and self.sysfs == Path('/sys'):
            return []
        rows = []
        for mount in self.mounts():
            if mount.root != '/' or mount.filesystem not in ('vfat', 'msdos') or 'rw' not in mount.options:
                continue
            try:
                # Prove the physical USB parent before touching the mount
                # path. Unrelated NFS/FUSE/removable mounts are not probed.
                self.block_node(mount)
                identity, _ = self.identify(mount.point)
            except (ValueError, OSError):
                continue
            rows.append({'root': identity.drive, 'label': 'Memory Stick PSP Dock · ' + identity.drive})
        return rows

    def hid_identity(self, path) -> str:
        self.check_access()
        candidate = Path(path)
        if candidate.parent != self.dev or not re.fullmatch(r'hidraw[0-9]+', candidate.name):
            raise NativeDockError('Ruta hidraw fuera del Dock.')
        node = self.sysfs / 'class/hidraw' / candidate.name / 'device'
        self.parent_of(node)
        if (node / 'report_descriptor').read_bytes() != REPORT_DESCRIPTOR:
            raise NativeDockError('El descriptor HID nativo difiere del esperado.')
        return str(node.resolve(strict=True))

    def paths(self) -> list[str]:
        if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1' and self.sysfs == Path('/sys'):
            return []
        self.check_access()
        paths = []
        for entry in sorted((self.sysfs / 'class/hidraw').glob('hidraw*')):
            path = str(self.dev / entry.name)
            try:
                self.hid_identity(path)
            except (ValueError, OSError):
                continue
            paths.append(path)
        return paths

    def mounts_for_parent(self, parent) -> list[Mount]:
        result = []
        for mount in self.mounts():
            try:
                actual = self.parent_of(self.sysfs / 'dev/block' / mount.number)
            except (ValueError, OSError):
                continue
            if actual == parent:
                result.append(mount)
        return result

    def eject(self, identity: PspStorage, parent: tuple[int, str]) -> dict:
        checked, _ = self.identify(identity.drive)
        if checked != identity or self.parent_of(checked.disk_id) != parent:
            raise NativeDockError('La Memory Stick cambió antes de desmontarla.')
        mounted = self.mounts_for_parent(parent)
        if len(mounted) != 1 or mounted[0].point != identity.drive:
            raise NativeDockError('Hay otros montajes del mismo Dock; ciérralos antes de salir.')
        device = self.block_device(mounted[0])
        try:
            result = self.run(['udisksctl', 'unmount', '--block-device', str(device),
                               '--no-user-interaction'], capture_output=True, text=True,
                              timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeDockError('UDisks2 no confirmó el desmontaje; la salida sigue pendiente.') from exc
        if result.returncode != 0:
            raise NativeDockError('UDisks2 rechazó el desmontaje. Cierra archivos abiertos y vuelve a salir del Dock.')
        if self.mounts_for_parent(parent):
            raise NativeDockError('La Memory Stick sigue montada; no se confirma la salida segura.')
        return {'host_eject_accepted': True, 'method': 'udisks2_unmount_verified',
                'volume_serial': identity.volume_serial, 'usb_parent': parent[1]}


def feature_ioctl(operation: int, length: int = 33) -> int:
    # asm-generic ioctl ABI used by supported x86_64/aarch64 Linux.
    return (3 << 30) | (length << 16) | (ord('H') << 8) | operation


class LinuxHid:
    def __init__(self, system=None):
        self.system = system or LinuxSystem()
        self.handles = {}

    def paths(self):
        return self.system.paths()

    @contextmanager
    def open(self, path, write=False):
        node = self.system.hid_identity(path)
        expected = (self.system.sysfs / 'class/hidraw' / Path(path).name / 'dev').read_text().strip()
        try:
            fd = os.open(path, (os.O_RDWR if write else os.O_RDONLY) | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        except PermissionError as exc:
            raise NativeDockError('Sin permiso hidraw. Instala la regla udev de PSP Dock y reconecta la PSP.') from exc
        try:
            info = os.fstat(fd)
            if (not stat.S_ISCHR(info.st_mode) or
                    f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' != expected or
                    self.system.hid_identity(path) != node):
                raise NativeDockError('El nodo hidraw cambió durante la apertura.')
            self.handles[fd] = path
            yield fd
        finally:
            self.handles.pop(fd, None)
            os.close(fd)

    def attributes(self, fd):
        self.system.hid_identity(self.handles[fd])
        return {'vendor_id': VID, 'product_id': PID, 'revision': REVISION}

    def get(self, fd):
        import fcntl
        report = bytearray(33)
        count = fcntl.ioctl(fd, feature_ioctl(0x07), report, True)
        if count != 33:
            raise NativeDockError('Respuesta hidraw PDK2 incompleta.')
        return decode_status(bytes(report))

    def set(self, fd, report):
        import fcntl
        if len(report) != 33 or report[0] != 0 or report[1:5] != MAGIC:
            raise NativeDockError('Orden PDK2 inválida.')
        count = fcntl.ioctl(fd, feature_ioctl(0x06), bytearray(report), True)
        if count != 33:
            raise NativeDockError('Entrega de fase incierta; no se repite la orden.')


def create_transport(system=None):
    system = system or LinuxSystem()
    factory = lambda: LinuxHid(system)
    return NativeDockTransport(
        probe=lambda: probe_native(api_factory=factory),
        identify_storage=system.identify, parent_of=system.parent_of,
        serial_of=system.serial_of,
        change_phase=lambda path, session, phase: request_phase(path, session, phase, api_factory=factory),
        eject=system.eject, hid_identity=system.hid_identity,
        same_root=lambda a, b: str(Path(a).absolute()) == str(Path(b).absolute()))
