"""Import UI sounds from one verified PSP export into the private PC cache.

No network, firmware image, external decoder or Sony assets are distributed.
Call only inside the native initial-storage callback. verify_device must
revalidate that callback's session, volume and storage ownership; its errors
are deliberately not swallowed as optional audio failures.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from psp_xmb_audio import RCO_SIZE, SOURCE_RELATIVE, extract_sounds
from xmb_sounds import SOUNDS, UiSoundPlayer


def cache_status(root: Path) -> dict:
    """Bounded PC-only check. Never play a sound or consult a PSP."""
    player = UiSoundPlayer(root, play=lambda _path: None)
    ready = all(player.asset(name) is not None for name in SOUNDS)
    return {'status': 'cached' if ready else 'waiting', 'ready': ready}


def _regular(path: Path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400 or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError('La importación de sonidos no admite enlaces ni rutas especiales.')
    return info


def _parents(path: Path, *, create=False) -> None:
    for folder in reversed((path, *path.parents)):
        if create and not folder.exists():
            folder.mkdir(mode=0o700)
        _regular(folder, directory=True)


def _read_export(root: Path) -> bytes:
    source = root / SOURCE_RELATIVE
    _parents(source.parent)
    before = _regular(source)
    if before.st_size != RCO_SIZE:
        raise ValueError('El recurso de sonidos no corresponde al formato compatible.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(source, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened):
            raise ValueError('El recurso cambió al abrirlo.')
        raw = stream.read(RCO_SIZE + 1)
        after = os.fstat(stream.fileno())
    _parents(source.parent)
    final = _regular(source)
    if (not os.path.samestat(opened, final) or
            (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or
            (after.st_size, after.st_mtime_ns) != (final.st_size, final.st_mtime_ns) or
            len(raw) != RCO_SIZE):
        raise ValueError('El recurso cambió durante su lectura.')
    return raw


def _write_new(path: Path, raw: bytes) -> None:
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise OSError('No se pudo verificar la caché de sonidos.')


def _verify_source(root: Path, verify_device) -> None:
    # Bind the already captured drive path as well as session/volume identity.
    # A reconnected card on another letter cannot authorize reading the old one.
    observed = verify_device()
    if observed is None or not Path(observed).is_absolute() or Path(observed).absolute() != root:
        raise ValueError('La raíz de la PSP cambió durante la importación de sonidos.')


def _cleanup_stage(stage: Path) -> None:
    # Only remove the files we created in our unique PC staging directory.
    # Never recurse through an arbitrary directory or touch a PSP source.
    try:
        _parents(stage)
        for name in (*SOUNDS.values(), 'manifest.json'):
            item = stage / name
            if item.exists():
                _regular(item)
                item.unlink()
        stage.rmdir()
    except (OSError, ValueError):
        pass


def _publish(stage: Path, target: Path) -> bool:
    """Publish a complete pack; preserve an incomplete previous directory."""
    _parents(target.parent)
    _regular(stage, directory=True)
    if cache_status(target)['ready']:
        return False
    backup = None
    if target.exists():
        _regular(target, directory=True)
        backup = target.with_name(target.name + '.previous-' + uuid.uuid4().hex)
        os.rename(target, backup)
    try:
        # rename, rather than replace, refuses a competing nonempty directory.
        os.rename(stage, target)
    except OSError:
        if backup is not None and not target.exists():
            os.rename(backup, target)
        raise
    return True


def import_from_psp(psp_root: Path, cache_root: Path, *, device: dict,
                    verify_device) -> dict:
    """Import once, preserving good cached sounds on every optional failure.

    The caller owns serialization. Source paths are fixed and read-only.
    All output goes to a separate PC directory; the source export is retained.
    """
    root, target = Path(psp_root).absolute(), Path(cache_root).absolute()
    if (not Path(psp_root).is_absolute() or not Path(cache_root).is_absolute() or
            '..' in Path(psp_root).parts or '..' in Path(cache_root).parts or
            str(target).startswith(('\\\\', '//')) or
            root == target or target.is_relative_to(root) or root.is_relative_to(target)):
        raise ValueError('La caché de sonidos debe estar separada de la PSP.')
    current = cache_status(target)
    if current['ready']:
        return current
    # A leftover file on a moved Memory Stick is not evidence of this PSP's
    # resource. 0308 attests READY in its session-bound status only after a
    # successful export or byte-for-byte comparison with current flash0.
    if device.get('ui_sounds_ready') is not True:
        return {'status': 'missing_export', 'ready': False}
    _verify_source(root, verify_device)
    try:
        raw = _read_export(root)
    except FileNotFoundError:
        return {'status': 'missing_export', 'ready': False}
    except (OSError, ValueError):
        return {'status': 'unavailable', 'ready': False}
    _verify_source(root, verify_device)
    try:
        sounds = extract_sounds(raw)
        if set(sounds) != set(SOUNDS):
            raise ValueError('Falta algún sonido del XMB.')
    except (ValueError, TypeError):
        return {'status': 'unsupported', 'ready': False}

    # Only identity fields already verified by NativeDockTransport are retained;
    # no absolute PC path or resource-provided metadata becomes a file path.
    provenance = {key: device[key] for key in
                  ('volume_serial', 'usb_parent', 'firmware_revision') if key in device}
    manifest = {
        'schema_version': 1,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source': {'type': 'connected_psp',
                   'resource': 'flash0:/vsh/resource/system_plugin.rco',
                   'export_relative': SOURCE_RELATIVE,
                   'rco_size': len(raw), 'rco_sha256': hashlib.sha256(raw).hexdigest(),
                   'profile': 'PSP 6.61 non-Go', 'device': provenance},
        'rights': {'redistribute': False, 'synthesized': False,
                   'assets': 'Original PSP resources; private local device import'},
        'conversion': {'codec': 'PS-ADPCM to PCM16 mono', 'resampled': False,
                       'filters': [], 'external_tools': []},
        'files': {SOUNDS[name]: {'size': len(wav), 'sha256': hashlib.sha256(wav).hexdigest()}
                  for name, wav in sounds.items()},
    }
    stage = target.with_name('.xmb-sounds-' + uuid.uuid4().hex + '.tmp')
    try:
        try:
            _parents(target.parent, create=True)
            stage.mkdir(mode=0o700)
            for name, wav in sounds.items():
                _write_new(stage / SOUNDS[name], wav)
            _write_new(stage / 'manifest.json',
                       (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
            if not cache_status(stage)['ready']:
                raise ValueError('No se pudo validar el paquete de sonidos completo.')
        except (OSError, ValueError, TypeError):
            return {'status': 'cache_unavailable', 'ready': False}
        # This check is outside optional-I/O exception handlers. A changed
        # device or cancelled storage operation must stop the workflow itself.
        _verify_source(root, verify_device)
        try:
            installed = _publish(stage, target)
        except (OSError, ValueError):
            return {'status': 'cache_unavailable', 'ready': False}
        return {'status': 'imported' if installed else 'cached', 'ready': True}
    finally:
        _cleanup_stage(stage)
