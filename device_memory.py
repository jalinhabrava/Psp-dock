"""PC-only preferences and metadata catalog cache, never write authorization.

Every connection still enumerates directory entries to detect additions,
removals and changed size/timestamps. Cache hits avoid parsing unchanged
EBOOT/ZIP metadata. Save comparisons and verified transfers bypass this cache.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import time

CHOICES = ('all', 'saves', 'games', 'none')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def metadata_fingerprint(root):
    root = Path(root)
    for folder in (root, root/'ISO', root/'PSP', root/'PSP/GAME'):
        try:
            info = folder.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('El catálogo no admite enlaces ni junctions.')
    rows = []
    pending = [(root/name, 0) for name in ('ISO', 'PSP/GAME')]
    while pending:
        folder, depth = pending.pop()
        if depth > 16:
            raise ValueError('El catálogo excede la profundidad de carpetas admitida.')
        try:
            with os.scandir(folder) as stream:
                entries = list(stream)
        except FileNotFoundError:
            rows.append((folder.relative_to(root).as_posix(), 'absent'))
            continue
        for item in entries:
            info = item.stat(follow_symlinks=False)
            if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('El catálogo no admite enlaces ni junctions.')
            path = Path(item.path)
            directory = stat.S_ISDIR(info.st_mode)
            if not directory and not stat.S_ISREG(info.st_mode):
                raise ValueError('El catálogo contiene un archivo no regular.')
            # Directory mtimes can be lazily published by Windows after the
            # last child handle closes. Names/types + ALL child file metadata
            # already detect every catalog addition/removal; ignore dir times.
            rows.append((path.relative_to(root).as_posix(), 'd' if directory else 'f',
                         0 if directory else info.st_size, 0 if directory else info.st_mtime_ns))
            if len(rows) > 32768:
                raise ValueError('El catálogo excede el límite de archivos admitido.')
            if directory:
                pending.append((path, depth+1))
    return _hash(sorted(rows))


class DeviceMemory:
    def __init__(self, directory, write):
        self.directory = Path(directory)
        self.write = write
        self.preferences = {}
        self.stats = {'hits':0, 'misses':0}
        file = self.directory/'device-preferences.json'
        try:
            if file.stat().st_size <= 131072:
                value = json.loads(file.read_text(encoding='utf-8'))
                if isinstance(value, dict):
                    self.preferences = {k:v for k,v in value.items()
                        if isinstance(k,str) and len(k)==64 and isinstance(v,dict)
                        and v.get('choice') in CHOICES and type(v.get('automatic')) is bool}
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def identity(current, config):
        if not current or not current.get('usb_parent') or not current.get('volume_serial'):
            return None
        return _hash([current['usb_parent'].upper(), current['volume_serial'],
                      str(config['library']), str(config['memstick'])])

    def preference(self, current, config):
        return dict(self.preferences.get(self.identity(current, config), {}))

    def remember(self, current, config, choice, automatic):
        key = self.identity(current, config)
        if not key or choice not in CHOICES or type(automatic) is not bool:
            raise ValueError('No se puede recordar esta elección sin la identidad de la PSP.')
        updated = {**self.preferences, key:{'choice':choice,'automatic':automatic}}
        if len(updated)>128:
            raise ValueError('Se alcanzó el límite de PSP recordadas.')
        self.write(self.directory/'device-preferences.json', updated)
        self.preferences = updated

    def inventory(self, root, serial, kind, scan):
        # Caller checks the root and live card. These are catalog metadata,
        # not a baseline for saves or a substitute for transfer hashes.
        key = _hash([str(serial), str(Path(root).absolute()), kind])
        file = self.directory/'catalog-cache'/(key+'.json')
        before = metadata_fingerprint(root)
        try:
            if file.stat().st_size <= 8*1024*1024:
                cached = json.loads(file.read_text(encoding='utf-8'))
                if (cached.get('schema') == 1 and cached.get('fingerprint') == before
                        and cached.get('kind') == kind and cached.get('volume') == serial
                        and type(cached.get('at')) in (int,float)
                        and 0 <= time.time()-cached['at'] < 86400
                        and cached.get('checksum') == _hash(cached.get('data'))
                        and isinstance(cached.get('data'), list if kind=='games' else dict)):
                    self.stats['hits'] += 1
                    return deepcopy(cached['data'])
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.stats['misses'] += 1
        result = scan()
        if metadata_fingerprint(root) != before:
            raise ValueError('La biblioteca cambió durante el inventario; vuelve a comprobarla.')
        self.write(file, {'schema':1,'fingerprint':before,'kind':kind,'volume':serial,
            'at':time.time(),'checksum':_hash(result),'data':result})
        return result
