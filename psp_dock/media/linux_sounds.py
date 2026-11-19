"""Play already verified, private XMB PCM effects through the Linux audio stack.

UiSoundPlayer owns asset/hash validation. This adapter owns at most one child
process; replacing an effect or closing the Dock never touches another app.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


class LinuxSoundOutput:
    def __init__(self, *, which=shutil.which, spawn=subprocess.Popen):
        self.executable = next((path for name in ('pw-play', 'paplay', 'aplay')
                                if (path := which(name))), None)
        self.spawn = spawn
        self.process = None

    def _stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.15)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=0.15)
                except subprocess.TimeoutExpired as exc:
                    # Preserve ownership for a later bounded cleanup attempt.
                    self.process = process
                    raise OSError('El reproductor de sonido sigue terminando.') from exc
        else:
            process.wait()

    def play(self, path: Path | None):
        self._stop()
        if path is None:
            return
        if self.executable is None:
            raise OSError('Instala pw-play, paplay o aplay para escuchar los sonidos del XMB.')
        if not Path(path).is_absolute():
            raise ValueError('El sonido debe pertenecer a la biblioteca privada del Dock.')
        self.process = self.spawn([self.executable, str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
