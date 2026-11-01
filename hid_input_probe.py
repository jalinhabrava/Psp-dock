"""Read-only PSP Dock HID input reporter for testing buttons and analog stick.

This tool NEVER issues HID SET_FEATURE or changes the USB role.
It queries the HID GET_REPORT(INPUT) request against a unique, verified
PSP Dock PDK1 device, after checking its descriptor and PDK1 session.
An unsupported GET_REPORT is reported explicitly; it does not imply that
interrupt input or the joystick is broken.

The expected 8-byte input payload is derived from the existing FuSa-style
PSP Dock report descriptor, NOT an arbitrary generic joystick layout.
"""
from __future__ import annotations

import argparse
import ctypes as C
from dataclasses import dataclass, asdict
import json
import os
import time
from typing import Callable

from bridge_hid import (
    _EXPECTED_PATH, _WindowsHid, BridgeHidError, decode_status,
    WINDOWS_REPORT_SIZE,
)


class InputProbeError(ValueError):
    pass


BUTTON_NAMES = (
    "CROSS", "CIRCLE", "SQUARE", "TRIANGLE",
    "L", "R", "SELECT", "START",
    "HOME", "VOL_DOWN", "VOL_UP", "SCREEN",
)
INPUT_SIZE_WITH_REPORT_ID = 9


@dataclass(frozen=True)
class InputReport:
    axes: tuple[int, int, int, int]
    hat: int
    buttons_mask: int
    pressed: tuple[str, ...]
    reserved_zero: bool


def decode_input_report(data: bytes) -> InputReport:
    """Decode one exact HID report; no manufacturer input inference."""
    if len(data) != INPUT_SIZE_WITH_REPORT_ID or data[0] != 0:
        raise InputProbeError("Informe HID: tamaño o Report ID inesperado.")
    payload = data[1:]
    axes = tuple(int(b) for b in payload[:4])
    # 4-bit hat followed by 12 buttons and two reserved bytes.
    hat = payload[4] & 0x0F
    buttons = ((payload[4] >> 4) & 0x0F) | (payload[5] << 4)
    if hat > 8 or payload[6] != 0 or payload[7] != 0:
        raise InputProbeError("Informe HID: POV o padding inesperado.")
    pressed = tuple(name for i, name in enumerate(BUTTON_NAMES)
                    if buttons & (1 << i))
    return InputReport(axes, hat, buttons, pressed, True)


def validate_unique_device(
    paths: list[str], open_status: Callable[[str], dict],
) -> tuple[str, str]:
    """Filter exact VID/PID first, then verify PDK1 one device only."""
    candidates = [p for p in paths if _EXPECTED_PATH.match(p)]
    if len(candidates) != 1:
        raise InputProbeError(
            "Se necesita UN HID PSP Dock (VID 054C, PID 01CB) para la lectura."
        )
    path = candidates[0]
    status = open_status(path)
    if not status.get("connected") or not isinstance(status.get("session"), str):
        raise InputProbeError("Sesión PDK1 no conectada o inválida.")
    if len(status["session"]) != 32:
        raise InputProbeError("Sesión PDK1 mal formada.")
    return path, status["session"]


def get_windows_input_report(hid_api: _WindowsHid, path: str,
                             expected_session: str) -> InputReport:
    """Issue HID GET_REPORT(INPUT) only; no SET_FEATURE or USB mutation."""
    if os.name != "nt":
        raise InputProbeError("La lectura INPUT requiere Windows.")
    dll = C.WinDLL("hid", use_last_error=True)
    get_input = dll.HidD_GetInputReport
    get_input.argtypes = [C.c_void_p, C.c_void_p, C.c_ulong]
    get_input.restype = C.c_ubyte
    with hid_api.open(path, write=False) as handle:
        status = hid_api.get(handle)  # Read-only session check on the SAME handle.
        if status.session != expected_session or not status.connected:
            raise InputProbeError("La sesión HID cambió durante la lectura.")
        report = C.create_string_buffer(INPUT_SIZE_WITH_REPORT_ID)
        if not get_input(handle, report, len(report)):
            raise InputProbeError(
                "Windows/HID no acepta GET_REPORT(INPUT): "
                + str(C.WinError(C.get_last_error()))
                + ". El joystick por interrupciones puede funcionar igualmente."
            )
        return decode_input_report(report.raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lectura manual PSP Dock HID INPUT (sin enviar comandos)")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()
    if not 1 <= args.samples <= 300 or not 0.1 <= args.interval <= 10:
        parser.error("Límites: samples 1-300, interval 0.1-10s.")
    try:
        api = _WindowsHid()

        def read_status(path: str) -> dict:
            with api.open(path, write=False) as handle:
                return asdict(api.get(handle))

        path, session = validate_unique_device(api.paths(), read_status)
        print("PSP Dock HID PDK1 conectada, sesión", session)
        print("GET_REPORT(INPUT), no se envían comandos ni se cambia USB.")
        previous: InputReport | None = None
        for n in range(args.samples):
            item = get_windows_input_report(api, path, session)
            if item != previous:
                print(json.dumps({"sample": n, **asdict(item)}, ensure_ascii=False))
                previous = item
            if n + 1 < args.samples:
                time.sleep(args.interval)
        print("Terminado. Botones neutros = buttons_mask: 0.")
        return 0
    except (InputProbeError, BridgeHidError, OSError, RuntimeError) as exc:
        print("Lectura HID INPUT no disponible:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
