"""Play a user's PC-cached game preview without browser autoplay restrictions.

Windows MCI uses a separate MPEG-audio channel, so brief XMB navigation sounds
can play without replacing the music. No Sony media or decoder is distributed.
This player never scans a PSP or converts an AT3 on demand.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import threading


def _regular_file(path: Path) -> bool:
    try:
        for node in (path, *path.parents):
            info = node.lstat()
            if (stat.S_ISLNK(info.st_mode) or
                    getattr(info, 'st_file_attributes', 0) & 0x400):
                return False
        return stat.S_ISREG(path.stat().st_mode)
    except (OSError, ValueError):
        return False


class PreviewAudioPlayer:
    """One bounded, process-owned, looping MCI channel for original MP3s."""

    def __init__(self, *, command=None):
        self._lock = threading.Lock()
        self._current: Path | None = None
        self._alias = 'pspdock_preview_' + str(os.getpid())
        self._command = command
        if command is None and os.name == 'nt':
            import ctypes
            mci = ctypes.WinDLL('winmm', use_last_error=True).mciSendStringW
            mci.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
            mci.restype = ctypes.c_uint
            self._command = lambda statement: mci(statement, None, 0, None)

    @property
    def supported(self) -> bool:
        return self._command is not None

    def _send(self, statement: str) -> bool:
        try:
            return self._command(statement) == 0
        except (OSError, RuntimeError):
            return False

    def _stop(self) -> None:
        if self._current is not None:
            self._send('stop ' + self._alias)
            self._send('close ' + self._alias)
            self._current = None

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def play(self, source: Path) -> bool:
        if not self.supported:
            return False
        source = Path(source).absolute()
        if not _regular_file(source) or source.suffix.lower() != '.mp3':
            return False
        try:
            valid_size = 128 <= source.stat().st_size <= 2 * 1024 * 1024
        except OSError:
            return False
        if not valid_size:
            return False
        with self._lock:
            if self._current == source:
                return True
            self._stop()
            # A fixed cache filename contains no quotes or untrusted MCI syntax.
            if '"' in str(source) or not self._send(
                    'open "' + str(source) + '" type mpegvideo alias ' + self._alias):
                return False
            self._current = source
            # MCI volume uses 0–1000. Failure here should not disable audio.
            self._send('setaudio ' + self._alias + ' volume to 220')
            if not self._send('play ' + self._alias + ' repeat'):
                self._stop()
                return False
            return True
