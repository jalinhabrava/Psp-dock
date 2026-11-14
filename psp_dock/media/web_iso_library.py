"""Read-only metadata for a user-selected, fixed Windows ISO folder.

No image contents are opened, hashed, copied, uploaded or served over HTTP.
Public scan/match reports contain basenames and opaque IDs, never file paths.
An ID describes a path AND its current stat metadata; it is not permission to
read that file. Resolution requires a fresh scan and explicit confirmation.
"""
from __future__ import annotations

import ctypes
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import unicodedata


class WebIsoLibraryError(ValueError):
    """The selection or metadata operation cannot be completed safely."""


SUPPORTED_EXTENSIONS = frozenset({'.iso', '.cso', '.bin', '.cue', '.chd'})
DEFAULT_MAX_ENTRIES = 20000
DEFAULT_MAX_DEPTH = 8
MAX_ENTRIES = 50000
MAX_DEPTH = 32
_ID_RE = re.compile(r'iso_[0-9a-f]{32}\Z')
_REPARSE_POINT = 0x400
_RESERVED_NAME = re.compile(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)


def _require_windows():
    if os.name != 'nt':
        raise WebIsoLibraryError('La biblioteca ISO local requiere Windows.')


def _drive_type(anchor):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    return int(kernel.GetDriveTypeW(anchor))


def _lexical_folder(path):
    try:
        raw = os.fspath(path)
    except TypeError as exc:
        raise WebIsoLibraryError('Selecciona una carpeta local válida.') from exc
    if (not isinstance(raw, str) or not raw or len(raw) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in raw)
            or raw.startswith(('\\\\', '//', '\\??\\'))):
        raise WebIsoLibraryError('Selecciona una ruta absoluta en un disco local.')
    normalized = raw.replace('\\', '/')
    if normalized.startswith('//'):
        raise WebIsoLibraryError('No se admiten carpetas de red ni rutas de dispositivo.')
    parts = normalized.split('/')
    if re.match(r'^[a-zA-Z]:/', normalized):
        parts = parts[1:]
    for part in parts:
        if (part in ('.', '..') or any(c in part for c in ':*?"<>|')
                or (part and part[-1] in ' .')
                or _RESERVED_NAME.match(unicodedata.normalize('NFKC', part))):
            raise WebIsoLibraryError('La ruta contiene componentes no permitidos.')
    folder = Path(raw)
    if (not folder.is_absolute() or folder == Path(folder.anchor)
            or len(folder.parts) < 2):
        raise WebIsoLibraryError('Selecciona una carpeta absoluta, no la raíz del disco.')
    return folder


def _is_link(info):
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT))


def _identity(info):
    return info.st_dev, info.st_ino


def _directory_chain(folder):
    """Check original ancestors in order, before following/resolving anything."""
    last = None
    try:
        for parent in reversed((folder, *folder.parents)):
            last = parent.lstat()
            if _is_link(last):
                raise WebIsoLibraryError('No se admiten enlaces ni puntos de unión.')
            if not stat.S_ISDIR(last.st_mode):
                raise WebIsoLibraryError('La selección debe ser una carpeta existente.')
    except OSError as exc:
        raise WebIsoLibraryError('No se pudo verificar la carpeta seleccionada.') from exc
    return last


def _reject_excluded(folder, excluded_roots):
    # Callers may pass live PSP roots in addition to the DRIVE_FIXED check.
    for excluded in excluded_roots:
        if not isinstance(excluded, (str, os.PathLike)):
            raise WebIsoLibraryError('La exclusión de unidades no es válida.')
        if not os.fspath(excluded):
            continue
        other = Path(excluded)
        if other.is_absolute() and (folder == other or folder.is_relative_to(other)):
            raise WebIsoLibraryError('La biblioteca debe estar en el PC, no en la PSP.')


def validate_web_iso_folder(path, *, excluded_roots=()) -> Path:
    """Return an existing fixed local folder; never create or alter it.

    Windows is required in production. Tests on other hosts patch
    _require_windows and _drive_type, without changing pathlib/os globally.
    """
    _require_windows()
    folder = _lexical_folder(path)
    _reject_excluded(folder, excluded_roots)
    try:
        if _drive_type(folder.anchor) != 3:  # DRIVE_FIXED; PSP USB is removable.
            raise WebIsoLibraryError('Se requiere un disco fijo local, no la PSP ni una red.')
        before = _directory_chain(folder)
        resolved = folder.resolve(strict=True)
        _lexical_folder(resolved)
        _reject_excluded(resolved, excluded_roots)
        if _drive_type(resolved.anchor) != 3:
            raise WebIsoLibraryError('Se requiere un disco fijo local.')
        after = _directory_chain(resolved)
        if (_identity(before) != _identity(after)
                or _identity(_directory_chain(folder)) != _identity(before)):
            raise WebIsoLibraryError('La carpeta cambió durante la validación.')
    except OSError as exc:
        raise WebIsoLibraryError('No se pudo verificar el disco local.') from exc
    return resolved


def choose_web_iso_folder(*, run=None, excluded_roots=()) -> str | None:
    """Native click-initiated picker; return the chosen folder itself, or None."""
    _require_windows()
    command = (
        "$ErrorActionPreference='Stop'; "
        '[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); '
        'Add-Type -AssemblyName System.Windows.Forms; '
        '$dialog=New-Object System.Windows.Forms.FolderBrowserDialog; '
        '$dialog.Description="Carpeta del PC que contiene tus imágenes de juegos"; '
        '$dialog.ShowNewFolderButton=$false; '
        '$dialog.RootFolder=[System.Environment+SpecialFolder]::MyComputer; '
        'try { if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) '
        '{ [Console]::WriteLine("PSPDOCK-ISO-FOLDER:"+$dialog.SelectedPath) } } '
        'finally { $dialog.Dispose() }'
    )
    try:
        result = (run or subprocess.run)(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-STA', '-Command', command],
            capture_output=True, text=True, encoding='utf-8', errors='strict',
            timeout=150, creationflags=0x08000000, shell=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise WebIsoLibraryError('No se pudo abrir el selector de carpetas de Windows.') from exc
    if result.returncode != 0:
        raise WebIsoLibraryError('El selector de carpetas falló en Windows.')
    output = result.stdout
    if not isinstance(output, str) or len(output) > 16384:
        raise WebIsoLibraryError('El selector devolvió una respuesta no válida.')
    if not output.strip():
        return None
    lines = output.splitlines()
    prefix = 'PSPDOCK-ISO-FOLDER:'
    if len(lines) != 1 or not lines[0].startswith(prefix):
        raise WebIsoLibraryError('Windows no devolvió una única carpeta.')
    return str(validate_web_iso_folder(lines[0][len(prefix):],
                                       excluded_roots=excluded_roots))


def _entry_id(root, relative, info):
    # Only short path/stat metadata enters this digest, never image bytes.
    payload = [os.path.normcase(str(root)), os.path.normcase(relative.as_posix()),
               info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino]
    return 'iso_' + hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    ).hexdigest()[:32]


def _limits(max_entries, max_depth, max_seconds):
    if type(max_entries) is not int or not 1 <= max_entries <= MAX_ENTRIES:
        raise WebIsoLibraryError('El límite de entradas debe estar entre 1 y 50000.')
    if type(max_depth) is not int or not 0 <= max_depth <= MAX_DEPTH:
        raise WebIsoLibraryError('La profundidad debe estar entre 0 y 32.')
    if (type(max_seconds) not in (int, float) or not math.isfinite(max_seconds)
            or not 0 < max_seconds <= 30):
        raise WebIsoLibraryError('El tiempo de escaneo debe ser mayor que 0 y hasta 30 segundos.')


def _scan(path, max_entries, max_depth, max_seconds, excluded_roots):
    _limits(max_entries, max_depth, max_seconds)
    root = validate_web_iso_folder(path, excluded_roots=excluded_roots)
    root_identity = _identity(_directory_chain(root))
    pending = [(root, 0, root_identity)]
    rows, locations, reasons = [], {}, set()
    skipped = {'links': 0, 'unsupported': 0, 'unreadable': 0, 'special': 0}
    visited = directories = 0
    deadline = time.monotonic() + max_seconds

    def check_directory(folder, expected):
        if (_identity(_directory_chain(root)) != root_identity
                or not folder.is_relative_to(root)
                or _identity(_directory_chain(folder)) != expected):
            raise WebIsoLibraryError('La biblioteca cambió durante el escaneo.')

    while pending:
        if visited >= max_entries:
            reasons.add('max_entries')
            break
        if time.monotonic() >= deadline:
            reasons.add('max_seconds')
            break
        folder, depth, expected = pending.pop()
        check_directory(folder, expected)
        directories += 1
        try:
            with os.scandir(folder) as stream:
                for item in stream:
                    if visited >= max_entries:
                        reasons.add('max_entries')
                        break
                    if time.monotonic() >= deadline:
                        reasons.add('max_seconds')
                        break
                    # Count every entry, including irrelevant files and links.
                    visited += 1
                    child = folder / item.name
                    try:
                        # Windows DirEntry.stat() has zero identity fields.
                        # lstat is non-following and matches subsequent checks.
                        info = child.lstat()
                    except OSError:
                        skipped['unreadable'] += 1
                        continue
                    if _is_link(info):
                        skipped['links'] += 1
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= max_depth:
                            reasons.add('max_depth')
                        else:
                            pending.append((child, depth + 1, _identity(info)))
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        skipped['special'] += 1
                        continue
                    extension = child.suffix.lower()
                    if extension not in SUPPORTED_EXTENSIONS:
                        skipped['unsupported'] += 1
                        continue
                    relative = child.relative_to(root)
                    entry_id = _entry_id(root, relative, info)
                    if entry_id in locations:
                        raise WebIsoLibraryError('El escaneo contiene identificadores ambiguos.')
                    rows.append({'id': entry_id, 'name': item.name, 'size': info.st_size,
                                 'mtime': info.st_mtime, 'extension': extension})
                    locations[entry_id] = child
        except OSError:
            skipped['unreadable'] += 1
        check_directory(folder, expected)
        if reasons.intersection({'max_entries', 'max_seconds'}):
            break
    check_directory(root, root_identity)
    rows.sort(key=lambda entry: (entry['name'].casefold(), entry['id']))
    report = {
        'entries': rows, 'count': len(rows), 'scanned_entries': visited,
        'scanned_directories': directories, 'truncated': bool(reasons),
        'complete': not reasons and not skipped['unreadable'],
        'limit_reasons': sorted(reasons), 'skipped': skipped,
        'limits': {'max_entries': max_entries, 'max_depth': max_depth,
                   'max_seconds': max_seconds},
        'metadata_only': True, 'requires_confirmation': True,
    }
    return report, locations, root


def scan_web_iso_folder(path, max_entries=DEFAULT_MAX_ENTRIES,
                        max_depth=DEFAULT_MAX_DEPTH, *, max_seconds=5,
                        excluded_roots=()) -> dict:
    """Bounded recursive stat-only scan; zip/7z archives are not inspected.

    Depth zero lists only the selected folder. All encountered entries count
    against max_entries. A large image costs one stat, irrespective of size.
    Limits/errors are reported explicitly; an incomplete result is not proof
    that there are no other images. Paths are intentionally absent.
    """
    return _scan(path, max_entries, max_depth, max_seconds, excluded_roots)[0]


def resolve_web_iso_entry(path, entry_id, *, confirmed=False,
                          max_entries=DEFAULT_MAX_ENTRIES,
                          max_depth=DEFAULT_MAX_DEPTH, max_seconds=5,
                          excluded_roots=()) -> Path:
    """Resolve an explicitly confirmed ID for native UI use; never open it.

    This is not an HTTP-serving API or authorization for a remote website.
    A later content reader would need its own fresh, handle-based validation.
    """
    if confirmed is not True:
        raise WebIsoLibraryError('Confirma manualmente la imagen antes de seleccionarla.')
    if not isinstance(entry_id, str) or not _ID_RE.fullmatch(entry_id):
        raise WebIsoLibraryError('El identificador de la imagen no es válido.')
    _, locations, root = _scan(path, max_entries, max_depth, max_seconds, excluded_roots)
    candidate = locations.get(entry_id)
    if candidate is None:
        raise WebIsoLibraryError('La imagen ya no está en el índice; vuelve a escanear.')
    try:
        _directory_chain(candidate.parent)
        info = candidate.lstat()
    except OSError as exc:
        raise WebIsoLibraryError('La imagen cambió; vuelve a escanear.') from exc
    if (_is_link(info) or not stat.S_ISREG(info.st_mode)
            or _entry_id(root, candidate.relative_to(root), info) != entry_id):
        raise WebIsoLibraryError('La imagen cambió; vuelve a escanear.')
    return candidate


_REGION_LANG = frozenset((
    'usa us europe eu japan jp jpn world pal ntsc ntscu ntscj australia asia '
    'korea china taiwan brazil canada france germany italy spain uk russia '
    'en ja jp fr de es it pt nl sv no da fi ko zh ru pl cs el tr ar he '
    'english japanese french german spanish italian portuguese'
).split())
_ROMAN_NUMERALS = frozenset(('i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x'))


def _release_annotation(match):
    value = match.group(1).strip().casefold()
    tokens = re.findall(r'[a-z]+', value)
    if ((tokens and not re.search(r'\d', value)
         and all(token in _REGION_LANG for token in tokens))
            or re.fullmatch(r'(?:rev(?:ision)?|disc|disk|track)\s*[\d.]+', value)
            or re.fullmatch(r'(?:slus|sles|slps|slpm|scus|sces|ulus|ules|uljm)[-_ ]?\d+', value)
            or value == '!'):
        return ' '
    return match.group(0)


def _title_key(value, *, filename=False):
    if not isinstance(value, str) or not 0 < len(value) <= 512:
        return ''
    if filename:
        if ('/' in value or '\\' in value or ':' in value
                or Path(value).suffix.lower() not in SUPPORTED_EXTENSIONS):
            return ''
        value = Path(value).stem
        value = re.sub(r'[\[(]([^\[\]()]+)[\])]', _release_annotation, value)
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = value.replace("'", '').replace('\u2019', '')
    tokens = ''.join(c if c.isalnum() else ' ' for c in value).split()
    return ' '.join(tokens)


def _edition_numbers(key):
    # Do not equate "Mega Man X" with "Mega Man 10". Roman/Arabic equivalents
    # must be supplied as explicit aliases for the particular catalogue game.
    return re.findall(r'\d+|\b(?:' + '|'.join(sorted(_ROMAN_NUMERALS)) + r')\b', key)


def match_web_iso_candidates(catalog_game, entries, *, max_candidates=20) -> dict:
    """Suggest by title/explicit aliases, never silently select a file.

    Confidence is filename similarity, not content or platform verification.
    Different sequels are excluded; regions/discs/formats remain ambiguous.
    """
    if not isinstance(catalog_game, dict):
        raise WebIsoLibraryError('El juego del catálogo no es válido.')
    if type(max_candidates) is not int or not 1 <= max_candidates <= 100:
        raise WebIsoLibraryError('El límite de sugerencias no es válido.')
    aliases = catalog_game.get('iso_aliases', catalog_game.get('aliases', []))
    if not isinstance(aliases, (list, tuple)):
        aliases = []
    labels = [(catalog_game.get('title'), 'title')]
    labels.extend((alias, 'alias') for alias in aliases[:32])
    targets = [(value, _title_key(value), kind) for value, kind in labels]
    targets = [(value, key, kind) for value, key, kind in targets if key]
    report = entries if isinstance(entries, dict) else None
    source = report.get('entries', []) if report is not None else entries
    if not isinstance(source, (list, tuple)) or len(source) > MAX_ENTRIES:
        raise WebIsoLibraryError('El índice de imágenes no es válido.')
    candidates, seen = [], set()
    for entry in source:
        if not isinstance(entry, dict):
            continue
        entry_id, name = entry.get('id'), entry.get('name')
        if (not isinstance(entry_id, str) or not _ID_RE.fullmatch(entry_id)
                or entry_id in seen):
            continue
        seen.add(entry_id)
        key = _title_key(name, filename=True)
        if not key:
            continue
        best = None
        for value, target, kind in targets:
            if _edition_numbers(key) != _edition_numbers(target):
                continue
            if key == target:
                confidence = 0.99 if kind == 'title' else 0.97
            else:
                left, right = set(key.split()), set(target.split())
                if (min(len(left), len(right)) < 2
                        or len(left & right) / len(left | right) < 0.75
                        or SequenceMatcher(None, key, target, autojunk=False).ratio() < 0.94):
                    continue
                confidence = 0.85
            if best is None or confidence > best['confidence']:
                best = {'confidence': confidence,
                        'confidence_label': 'high' if confidence >= 0.95 else 'medium',
                        'matched_by': kind, 'matched_title': value}
        if best is not None:
            # Do not copy any unexpected paths/URLs from a caller-supplied row.
            candidates.append({
                **{field: entry.get(field) for field in ('id', 'name', 'size', 'mtime', 'extension')},
                **best, 'requires_confirmation': True, 'confirmed': False,
            })
    candidates.sort(key=lambda row: (-row['confidence'], row['name'].casefold(), row['id']))
    count = len(candidates)
    incomplete = report is not None and not report.get('complete', False)
    return {
        'status': 'ambiguous' if count > 1 else ('suggested' if count else 'none'),
        'candidates': candidates[:max_candidates], 'candidate_count': count,
        'suggested_id': candidates[0]['id'] if count == 1 and not incomplete else None,
        'ambiguous': count > 1, 'has_more': count > max_candidates,
        'scan_complete': None if report is None else not incomplete,
        'requires_confirmation': True, 'confirmed': False,
    }
