"""Short-lived, read-only directory snapshots for catalog comparisons.

Missing files are resolved from one scandir per directory, not by resolving
every ancestor of thousands of absent paths. Never use this as authority to
write, launch, or open file contents: those operations retain their fresh
path/identity/hash checks. Discard the instance after the current comparison.
"""
import os
from pathlib import Path
import stat


class ReadOnlyTree:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self._directories = {}
        self.scans = 0
        for path in reversed((self.root, *self.root.parents)):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            self._regular(info, directory=True)

    @staticmethod
    def _regular(info, *, directory=False):
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('El catálogo no admite enlaces ni junctions.')
        if directory and not stat.S_ISDIR(info.st_mode):
            raise ValueError('La raíz del catálogo no es una carpeta.')
        if not directory and not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('El catálogo contiene un archivo no regular.')

    def _listing(self, folder):
        if folder not in self._directories:
            self.scans += 1
            try:
                with os.scandir(folder) as stream:
                    rows = {}
                    for item in stream:
                        info = item.stat(follow_symlinks=False)
                        self._regular(info)
                        key = os.path.normcase(item.name)
                        if key in rows:
                            raise ValueError('Nombres ambiguos en el catálogo.')
                        rows[key] = info
            except FileNotFoundError:
                rows = {}
            self._directories[folder] = rows
        return self._directories[folder]

    def entry(self, relative):
        value = str(relative)
        path = Path(value)
        # Check both separator spellings, including on non-Windows tests.
        if (not value or path.is_absolute() or path.drive or ':' in value or
                '\x00' in value or any(part in ('', '.', '..') for part in value.replace('\\', '/').split('/'))):
            raise ValueError('Ruta de catálogo inválida.')
        parts = value.replace('\\', '/').split('/')
        target, here = self.root.joinpath(*parts), self.root
        for index, part in enumerate(parts):
            info = self._listing(here).get(os.path.normcase(part))
            if info is None:
                return target, None
            if index != len(parts)-1 and not stat.S_ISDIR(info.st_mode):
                return target, None
            here = here/part
        return target, info

    def file(self, relative):
        path, info = self.entry(relative)
        return path, info if info is not None and stat.S_ISREG(info.st_mode) else None
