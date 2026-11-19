"""Prove a Linux library destination is on fixed local storage, read-only."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from psp_dock.devices.linux_native import parse_mountinfo

LOCAL_FILESYSTEMS = frozenset({'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'f2fs'})


def fixed_local_directory(path, *, mountinfo=Path('/proc/self/mountinfo'), sysfs_root=Path('/sys')):
    original = Path(path)
    if not original.is_absolute():
        raise ValueError('La carpeta Linux debe ser absoluta.')
    for parent in (original, *original.parents):
        if parent.is_symlink():
            raise ValueError('No se admiten enlaces en las carpetas del Dock.')
    target = original.resolve()
    mounts = parse_mountinfo(Path(mountinfo).read_text(encoding='utf-8'))
    candidates = [m for m in mounts if target.is_relative_to(Path(m.point))]
    if not candidates:
        raise ValueError('No se puede identificar el disco de destino Linux.')
    depth = max(len(Path(m.point).parts) for m in candidates)
    visible = [m for m in candidates if len(Path(m.point).parts) == depth]
    if len(visible) != 1:
        raise ValueError('Hay montajes superpuestos en el destino Linux.')
    mount = visible[0]
    if mount.filesystem not in LOCAL_FILESYSTEMS or 'rw' not in mount.options:
        raise ValueError('Usa un disco fijo local con ext4, XFS, Btrfs o F2FS y escritura; no USB, red ni almacenamiento virtual.')
    if mount.root != '/' and mount.filesystem != 'btrfs':
        raise ValueError('No se admiten montajes bind para las carpetas del Dock.')
    # Path.resolve() does not reveal bind aliases. Prevent an alternate
    # mount of the same subtree from bypassing data-directory overlap checks.
    # Separate Btrfs subvolumes such as /@ and /@home remain admissible.
    logical = Path(mount.root) / target.relative_to(Path(mount.point))
    for other in mounts:
        if other is mount or other.number != mount.number:
            continue
        if logical.is_relative_to(Path(other.root)) or Path(other.root).is_relative_to(logical):
            raise ValueError('El disco expone rutas de montaje equivalentes; elige una carpeta sin alias bind.')
    root = Path(sysfs_root).resolve(strict=True)
    node = root / 'dev/block' / mount.number
    if not node.exists():
        # Btrfs may expose an anonymous mount major:minor. Its block-device
        # source still has a kernel identity; never open or read that disk.
        if mount.filesystem != 'btrfs' or not mount.source.startswith('/dev/'):
            raise ValueError('El destino no tiene una identidad de disco local verificable.')
        info = Path(mount.source).stat()
        if not stat.S_ISBLK(info.st_mode):
            raise ValueError('El origen Btrfs no es un dispositivo de bloque.')
        node = root / 'dev/block' / f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
    nodes = [node]
    if mount.filesystem == 'btrfs':
        # A Btrfs filesystem may span several devices. Proving only its
        # mount source would miss a second removable/USB member.
        matches = []
        for filesystem in (root / 'fs/btrfs').glob('*'):
            devices = filesystem / 'devices'
            if not devices.is_dir():
                continue
            members = [entry.resolve(strict=True) for entry in devices.iterdir()]
            if node.resolve(strict=True) in members:
                matches.append(members)
        if len(matches) != 1 or not matches[0]:
            raise ValueError('No se pueden verificar todos los discos del sistema Btrfs.')
        nodes = matches[0]
    checked, active = set(), set()

    def check_block(block):
        block = block.resolve(strict=True)
        if not block.is_relative_to(root) or block in active or len(checked | active) >= 64:
            raise ValueError('La cadena de discos del destino no es verificable.')
        if block in checked:
            return
        active.add(block)
        parents = (block, *block.parents)
        flags = []
        for parent in parents:
            if (parent / 'idVendor').exists() or (parent / 'idProduct').exists():
                raise ValueError('Los datos del PC no se guardan en la PSP ni en discos USB.')
            removable = parent / 'removable'
            if removable.is_file():
                flags.append(removable.read_text().strip())
        if not flags or any(flag != '0' for flag in flags):
            raise ValueError('El destino no se ha confirmado como disco fijo.')
        slaves = list((block / 'slaves').iterdir()) if (block / 'slaves').is_dir() else []
        if slaves:
            for slave in slaves:
                check_block(slave)
        elif 'virtual' in block.relative_to(root).parts:
            raise ValueError('El almacenamiento virtual requiere una cadena de discos físicos verificable.')
        active.remove(block)
        checked.add(block)

    for node in nodes:
        check_block(node)
    return target
