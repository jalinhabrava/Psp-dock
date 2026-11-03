"""Private original XMB WAV playback; no bundled Sony assets or synthesis.

The importer retains source provenance in manifest.json. This module only
accepts four bounded PCM files whose hashes match that local manifest.
Windows playback is asynchronous and does not depend on browser activation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import threading
import time
import wave

SOUNDS = {name: 'snd_' + name + '.wav' for name in ('cursor', 'decide', 'cancel', 'option')}
MAX_WAV = 512 * 1024


def _no_links(path: Path) -> None:
    for node in (path, *path.parents):
        if node.is_symlink() or (hasattr(node, 'is_junction') and node.is_junction()):
            raise ValueError('Los sonidos no admiten enlaces ni junctions.')


def validate_wav(data: bytes) -> None:
    if not 44 <= len(data) <= MAX_WAV:
        raise ValueError('Sonido fuera del tamaño permitido.')
    try:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            channels, width, rate, count = (audio.getnchannels(), audio.getsampwidth(),
                                          audio.getframerate(), audio.getnframes())
            if (audio.getcomptype() != 'NONE' or channels not in (1, 2) or width != 2 or
                    not 8000 <= rate <= 96000 or not 0 < count <= rate * 3):
                raise ValueError('Se requiere un efecto breve WAV PCM de 16 bits.')
            if len(audio.readframes(count)) != count * channels * width:
                raise ValueError('Sonido PCM truncado.')
    except (wave.Error, EOFError) as exc:
        raise ValueError('Sonido WAV no válido.') from exc


class UiSoundPlayer:
    def __init__(self, root: Path, *, play=None, clock=time.monotonic):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._clock = clock
        self._last_at = float('-inf')
        self._playing = False
        self._play = play
        if play is None and os.name == 'nt':
            import winsound
            self._play = lambda path: winsound.PlaySound(
                str(path) if path is not None else None,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
                if path is not None else 0)

    def asset(self, name: str) -> tuple[Path, bytes] | None:
        if not isinstance(name, str) or name not in SOUNDS:
            raise ValueError('Sonido de navegación desconocido.')
        file = self.root / SOUNDS[name]
        manifest = self.root / 'manifest.json'
        try:
            _no_links(file)
            _no_links(manifest)
            if not file.is_file() or not manifest.is_file() or manifest.stat().st_size > 65536:
                return None
            if not 44 <= file.stat().st_size <= MAX_WAV:
                return None
            record = json.loads(manifest.read_text(encoding='utf-8')).get('files', {}).get(file.name, {})
            data = file.read_bytes()
            if (record.get('size') != len(data) or
                    record.get('sha256') != hashlib.sha256(data).hexdigest()):
                return None
            validate_wav(data)
            return file, data
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            return None

    def play(self, name: str) -> bool:
        if not isinstance(name, str) or name not in SOUNDS:
            raise ValueError('Sonido de navegación desconocido.')
        if self._play is None:
            return False
        with self._lock:
            now = self._clock()
            if now - self._last_at < .055:
                return False
            asset = self.asset(name)
            if asset is None:
                return False  # Missing original stays silent, never a Windows beep.
            try:
                self._play(asset[0])
            except (OSError, RuntimeError):
                return False
            self._last_at, self._playing = now, True
            return True

    def stop(self) -> None:
        with self._lock:
            if self._playing and self._play is not None:
                try:
                    self._play(None)
                except (OSError, RuntimeError):
                    pass
            self._playing = False
