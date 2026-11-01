"""Read-only recognition of PSP homebrew emulators and their cartridge ROMs.

An EBOOT.PBP is not necessarily a PSP game. Only *known* emulator names
(folder or bounded PARAM.SFO title) are classified; unknown homebrew remains
in the regular PSP library. Content is never executed during discovery.

ROMs are inventoried by console, not by the PSP emulator's binary. Windows
equivalents are explicit RetroArch libretro cores installed by the user.
No ROM, core, BIOS or save is downloaded or automatically migrated.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import zipfile

from psp_game_metadata import read_metadata


SYSTEMS = {
    'gba': ('Game Boy Advance', 'mgba_libretro.dll'),
    'gb': ('Game Boy', 'gambatte_libretro.dll'),
    'gbc': ('Game Boy Color', 'gambatte_libretro.dll'),
    'snes': ('Super Nintendo', 'snes9x_libretro.dll'),
    'nes': ('NES / Famicom', 'mesen_libretro.dll'),
    'md': ('Mega Drive / Genesis', 'genesis_plus_gx_libretro.dll'),
    'sms': ('Master System', 'genesis_plus_gx_libretro.dll'),
    'gg': ('Game Gear', 'genesis_plus_gx_libretro.dll'),
    '32x': ('Sega 32X', 'picodrive_libretro.dll'),
    'n64': ('Nintendo 64', 'mupen64plus_next_libretro.dll'),
    'pce': ('PC Engine', 'mednafen_pce_fast_libretro.dll'),
    'a2600': ('Atari 2600', 'stella_libretro.dll'),
}

# Prefixes are normalized to letters/digits. Keep them unambiguous: folder
# "MyGame" and SFO "Homebrew Game" must never become an emulator by guessing.
EMULATORS = (
    ('gpSP Kai', ('uogpspkai', 'gpspkai', 'gpsp'), ('gba',)),
    ('TempGBA4PSP', ('tempgba4psp', 'tempgba'), ('gba',)),
    ('Snes9xTYL', ('snes9xtyl', 'snes9xme', 'snes9xcm'), ('snes',)),
    ('Snes9x Euphoria', ('snes9xeuphoria',), ('snes',)),
    ('NesterJ', ('nesterjaoex', 'nesterj'), ('nes',)),
    ('FCEUltra PSP', ('fceultrapsp', 'fceultra'), ('nes',)),
    ('MasterBoy', ('masterboy',), ('gb', 'gbc')),
    ('RIN / PSP GnuBoy', ('homerin', 'rinpsp', 'pspgnuboy', 'gnuboypsp'), ('gb', 'gbc')),
    ('PicoDrive', ('picodrive',), ('md', 'sms', 'gg', '32x')),
    ('SMS Plus PSP', ('smspluspsp', 'smsplus'), ('sms', 'gg')),
    ('DaedalusX64', ('daedalusx64', 'daedalus'), ('n64',)),
    ('PCE for PSP', ('pceforpsp', 'pcepsp'), ('pce',)),
    ('PSP2600', ('psp2600', 'stellapsp'), ('a2600',)),
)

EXTENSIONS = {
    '.gba': 'gba', '.gb': 'gb', '.gbc': 'gbc',
    '.sfc': 'snes', '.smc': 'snes', '.swc': 'snes',
    '.nes': 'nes', '.fds': 'nes',
    '.gen': 'md', '.md': 'md', '.smd': 'md',
    '.sms': 'sms', '.gg': 'gg', '.32x': '32x',
    '.z64': 'n64', '.n64': 'n64', '.v64': 'n64',
    '.pce': 'pce', '.a26': 'a2600',
}

SKIP_DIRS = frozenset({
    'saves', 'save', 'savestates', 'savestate', 'states', 'state',
    'config', 'configs', 'settings', 'bios', 'system', 'cheats',
    'screenshots', 'images', 'assets', 'skins', 'media', 'cache',
})
MAX_EMULATORS = 64
MAX_ENTRIES = 8192
MAX_ROMS = 2048
MAX_ROM_SIZE = 256 * 1024 * 1024


def _normal(text: str) -> str:
    return re.sub('[^a-z0-9]', '', text.casefold())


def emulator_profile(folder: Path, eboot: Path | None = None) -> dict | None:
    """Use strong aliases only; an unrecognized EBOOT is still a PSP title."""
    candidates = [_normal(Path(folder).name)]
    if eboot is not None and Path(eboot).is_file():
        metadata = read_metadata(eboot)
        if isinstance(metadata.get('TITLE'), str):
            candidates.append(_normal(metadata['TITLE']))
    for label, aliases, systems in EMULATORS:
        if any(value.startswith(alias) for value in candidates for alias in aliases):
            return {'name': label, 'systems': list(systems)}
    return None


def _safe(root: Path, path: Path) -> Path:
    """Refuse symlinks/junctions even when they resolve back inside root."""
    root = Path(root).absolute()
    path = Path(path).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Ruta retro fuera de la Memory Stick.')
    current = path
    while True:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('El catálogo retro no admite enlaces ni junctions.')
        if current == root:
            break
        current = current.parent
    return path


def _rom_system(path: Path, allowed: tuple[str, ...]) -> str | None:
    suffix = path.suffix.casefold()
    system = EXTENSIONS.get(suffix)
    if system in allowed:
        return system
    if suffix != '.zip' or path.stat().st_size > MAX_ROM_SIZE:
        return None
    # A ZIP of one ROM (+README etc.) is common on PSP. Check the central
    # directory only; do not extract, read its payload or trust its name.
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 128:
                return None
            content = [EXTENSIONS.get(Path(info.filename).suffix.casefold())
                       for info in entries if not info.is_dir() and
                       not info.flag_bits & 1 and info.file_size <= MAX_ROM_SIZE]
            relevant = [item for item in content if item in allowed]
            return relevant[0] if len(relevant) == 1 else None
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        return None


def emulator_inventory(root: Path) -> dict:
    """Bounded recursive inventory under known PSP/GAME emulator folders.

    No arbitrary files in GAME or in an unknown homebrew folder are treated
    as ROMs. In particular, ambiguous .bin, BIOS, .sav and disc images are
    ignored until a future explicitly configured platform adapter exists.
    """
    root = Path(root).absolute()
    game_root = _safe(root, root / 'PSP' / 'GAME')
    result = {'emulators': [], 'roms': []}
    if not game_root.is_dir():
        return result
    folders = sorted((p for p in game_root.iterdir() if p.is_dir()),
                     key=lambda item: item.name.casefold())
    if len(folders) > MAX_ENTRIES:
        raise ValueError('Demasiadas carpetas PSP/GAME para inventario retro seguro.')
    for folder in folders:
        _safe(root, folder)
        eboot = _safe(root, folder / 'EBOOT.PBP')
        if not eboot.is_file():
            continue
        profile = emulator_profile(folder, eboot)
        if not profile:
            continue
        if len(result['emulators']) >= MAX_EMULATORS:
            raise ValueError('Demasiados emuladores PSP para el catálogo retro.')
        relative = eboot.relative_to(root).as_posix()
        emulator_id = hashlib.sha256(('emulator:' + relative).encode('utf-8')).hexdigest()[:24]
        stat = eboot.stat()
        result['emulators'].append({
            'id': emulator_id, 'name': profile['name'], 'relative': relative,
            'systems': profile['systems'], 'size': stat.st_size,
            'modified_ns': stat.st_mtime_ns,
        })
        allowed = tuple(profile['systems'])
        seen = 0
        for current, dirs, files in os.walk(folder, followlinks=False):
            base = _safe(root, Path(current))
            depth = len(base.relative_to(folder).parts)
            if depth >= 4:
                dirs[:] = []
            else:
                dirs[:] = [name for name in dirs if _normal(name) not in SKIP_DIRS
                           and not (base / name).is_symlink()]
            seen += len(dirs) + len(files)
            if seen > MAX_ENTRIES:
                raise ValueError('Demasiados archivos en el emulador PSP.')
            for name in files:
                path = _safe(root, base / name)
                if not path.is_file() or path.stat().st_size > MAX_ROM_SIZE:
                    continue
                system = _rom_system(path, allowed)
                if system is None:
                    continue
                if len(result['roms']) >= MAX_ROMS:
                    raise ValueError('Demasiadas ROMs para el catálogo seguro.')
                relative_rom = path.relative_to(root).as_posix()
                stat = path.stat()
                result['roms'].append({
                    'id': hashlib.sha256(('rom:' + relative_rom).encode('utf-8')).hexdigest()[:24],
                    'relative': relative_rom, 'title': path.stem,
                    'system': system, 'system_name': SYSTEMS[system][0],
                    'emulator_id': emulator_id, 'format': path.suffix[1:].upper(),
                    'size': stat.st_size, 'modified_ns': stat.st_mtime_ns,
                })
    result['roms'].sort(key=lambda item: (item['emulator_id'], item['title'].casefold()))
    return result


def retroarch_core(retroarch: str, system: str) -> tuple[Path, Path] | None:
    """Return installed RetroArch executable + corresponding local core.

    Never download or run an executable inferred from a PSP filename.
    An explicit RetroArch installation is required on Windows.
    """
    if system not in SYSTEMS or not isinstance(retroarch, str) or not retroarch:
        return None
    exe = Path(retroarch)
    if not exe.is_absolute() or exe.name.casefold() != 'retroarch.exe' or not exe.is_file():
        return None
    core = exe.parent / 'cores' / SYSTEMS[system][1]
    if not core.is_file() or core.is_symlink() or core.parent.is_symlink():
        return None
    return exe, core
