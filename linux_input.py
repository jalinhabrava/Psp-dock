"""Read native PSP gamepad reports through a nonexclusive hidraw handle."""
from __future__ import annotations

import os
import threading

from linux_native import LinuxHid
from native_dock import STORAGE, CONTROLLER, NativeDockError
from psp_launcher_input import idle, normalize_psp_joystick


def decode_input(report: bytes) -> dict:
    # Unnumbered HID INPUT reports have NO leading report-ID byte in read().
    # Four 8-bit axes, 4-bit hat, 12 buttons, 16 padding bits.
    if not isinstance(report, bytes) or len(report) != 8 or report[6:] != b'\0\0':
        raise NativeDockError('Informe del mando Linux incompleto o desconocido.')
    hat = report[4] & 15
    if hat > 8:
        raise NativeDockError('Cruceta PSP fuera de rango.')
    result = normalize_psp_joystick(
        buttons=(report[4] >> 4) | (report[5] << 4),
        pov=(hat - 1) * 4500 if hat else 65535,
        x=report[0], y=report[1], x_range=(0, 255), y_range=(0, 255))
    return {**result, 'source': 'psp_linux_hidraw'}


class PSPLinuxReader:
    def __init__(self, *, api_factory=LinuxHid, read=os.read):
        self.api_factory, self._read = api_factory, read
        self._context = self._api = self._fd = self._key = self._last = None
        self._lock = threading.RLock()

    def close(self):
        with self._lock:
            if self._context is not None:
                self._context.__exit__(None, None, None)
            self._context = self._api = self._fd = self._key = self._last = None

    def read(self, observation):
        with self._lock:
            key = tuple(observation.get(k) for k in ('path', 'session_id', 'usb_parent'))
            try:
                if not all(isinstance(v, str) and v for v in key):
                    raise NativeDockError('No hay una sesión Linux verificada para el mando.')
                if key != self._key:
                    self.close()
                    api = self.api_factory()
                    context = api.open(key[0])
                    fd = context.__enter__()
                    self._api, self._context, self._fd, self._key = api, context, fd, key
                self._api.attributes(self._fd)  # Reject disconnect or changed sysfs identity.
                status = self._api.get(self._fd)
                if (status.session != key[1] or not status.connected or not status.sony_storage or
                        status.pending or status.result < 0 or status.phase not in (STORAGE, CONTROLLER)):
                    raise NativeDockError('La navegación está bloqueada durante la transición del Dock.')
                # Raw queues belong to this handle; kernel evdev/SDL retains
                # its own event stream. Never EVIOCGRAB or detach usbhid.
                for _ in range(256):
                    try:
                        report = self._read(self._fd, 64)
                    except BlockingIOError:
                        break
                    if not report:
                        raise NativeDockError('Se desconectó el mando PSP.')
                    self._last = decode_input(report)
                else:
                    raise NativeDockError('La cola del mando está saturada; libera los botones.')
                # Firmware sends changes, not periodic repeats. Keeping the
                # last state is safe only while this same PDK2 handle/session
                # still confirms a navigable phase on every poll.
                return dict(self._last) if self._last else {**idle('Esperando el primer movimiento del mando.'), 'source': 'psp_linux_hidraw'}
            except (ValueError, OSError, RuntimeError):
                self.close()
                raise
