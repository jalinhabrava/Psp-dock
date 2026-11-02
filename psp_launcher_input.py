"""Read-only PSP HID controller input for PSP Dock's Windows launcher.

Unlike a browser Gamepad API implementation, the Windows WinMM mapping is
known from the physically verified PSP Dock / FuSa controller. It selects a
single WinMM device by Sony VID 054C and our HID PID 01CB, never a gamepad
by name or device index alone.

NO USB role switch, writes, SET_FEATURE, FAT access, eject, or HID commands.
The separate, verified PDK1 status gate belongs to Dock.controller_status.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass, asdict
import os

from bridge_hid import VID, PID, MSC_TEST_PID, NATIVE_PID
from hid_winmm_buttons import (
    JOYCAPSW, JOYINFOEX, JoyProbeError, decode_joyinfo,
    matching_joystick_capabilities,
)


def idle(reason: str = "Esperando a PSP HID PDK1.") -> dict:
    return {
        "connected": False, "source": "psp_winmm", "reason": str(reason),
        "left": False, "right": False, "up": False, "down": False,
        "accept": False, "back": False, "saves": False,
        "graphics": False, "settings": False,
        "pageLeft": False, "pageRight": False,
        "exit": False, "button_mask": 0,
    }


def decode_directions(pov: int, *, x: int, y: int,
                      x_range: tuple[int, int] = (0, 65535),
                      y_range: tuple[int, int] = (0, 65535)) -> dict:
    """Map exact WinMM POV hundredths-of-degrees and analog axes.

    0=N, 9000=E, 18000=S, 27000=W. Diagonals activate two directions.
    Analog uses the device-specific reported range (not assumed 16-bit).
    """
    if type(pov) is not int or (pov not in (0xFFFF, 0xFFFFFFFF) and
                              not 0 <= pov < 36000):
        raise JoyProbeError("Ángulo POV de Windows no reconocido.")
    def normalized(value, bounds):
        start, end = bounds
        if (type(value) is not int or type(start) is not int or
                type(end) is not int or end <= start):
            raise JoyProbeError("Rango de eje WinMM desconocido.")
        return (value - (start + end) / 2.0) / ((end - start) / 2.0)
    horizontal, vertical = normalized(x, x_range), normalized(y, y_range)
    hat = None if pov in (0xFFFF, 0xFFFFFFFF) else pov
    return {
        "left": horizontal < -0.48 or hat is not None and 22500 <= hat <= 31500,
        "right": horizontal > 0.48 or hat is not None and 4500 <= hat <= 13500,
        "up": vertical < -0.48 or hat is not None and (hat >= 31500 or hat <= 4500),
        "down": vertical > 0.48 or hat is not None and 13500 <= hat <= 22500,
    }


def normalize_psp_joystick(*, buttons: int, pov: int, x: int, y: int,
                           x_range: tuple[int, int] = (0, 65535),
                           y_range: tuple[int, int] = (0, 65535)) -> dict:
    """Physical 1..8: CROSS, CIRCLE, SQUARE, TRIANGLE, L, R, SELECT, START."""
    if type(buttons) is not int or not 0 <= buttons <= 0xFFF:
        raise JoyProbeError("Máscara de botones PSP fuera de los 12 admitidos.")
    controls = decode_directions(pov, x=x, y=y, x_range=x_range, y_range=y_range)
    pressed = lambda index: bool(buttons & (1 << index))
    return {
        "connected": True, "source": "psp_winmm", "reason": "",
        **controls,
        "accept": pressed(0),        # PSP CROSS
        "back": pressed(1),          # PSP CIRCLE
        "saves": pressed(2),         # PSP SQUARE
        "graphics": pressed(3),       # TRIANGLE: per-game graphic settings
        "settings": pressed(7),       # START: Dock settings
        "pageLeft": pressed(4),      # L
        "pageRight": pressed(5),     # R
        "exit": pressed(6),          # SELECT: focus the exit action
        "button_mask": buttons,
    }


class PSPWinMMReader:
    """One short host-only joystick snapshot; safe under concurrent HTTP GETs."""

    def __init__(self, *, expected_product: int = PID):
        if type(expected_product) is not int or expected_product not in (PID, MSC_TEST_PID, NATIVE_PID):
            raise JoyProbeError('PID PSP Dock no reconocido.')
        self.expected_product = expected_product

    def read(self) -> dict:
        if os.name != "nt":
            return idle("WinMM solo está disponible en Windows.")
        winmm = C.WinDLL("winmm", use_last_error=True)
        winmm.joyGetNumDevs.argtypes = []
        winmm.joyGetNumDevs.restype = W.UINT
        winmm.joyGetDevCapsW.argtypes = [C.c_size_t, C.POINTER(JOYCAPSW), W.UINT]
        winmm.joyGetDevCapsW.restype = W.UINT
        winmm.joyGetPosEx.argtypes = [W.UINT, C.POINTER(JOYINFOEX)]
        winmm.joyGetPosEx.restype = W.UINT
        rows = []
        ranges: dict[int, tuple[tuple[int,int],tuple[int,int]]] = {}
        for index in range(min(16, winmm.joyGetNumDevs())):
            caps = JOYCAPSW()
            if winmm.joyGetDevCapsW(index, C.byref(caps), C.sizeof(caps)) == 0:
                rows.append({
                    "index": index, "vendor_id": caps.wMid,
                    "product_id": caps.wPid, "buttons": caps.wNumButtons,
                })
                ranges[index] = ((caps.wXmin, caps.wXmax),
                                 (caps.wYmin, caps.wYmax))
        target = matching_joystick_capabilities(rows, expected_product=self.expected_product)
        if target["vendor_id"] != VID or target["product_id"] != self.expected_product:
            raise JoyProbeError("El mando no es PSP Dock HID PDK1.")
        if target["buttons"] < 8:
            raise JoyProbeError("El descriptor de mando PSP no tiene ocho botones.")
        info = JOYINFOEX()
        info.dwSize = C.sizeof(info)
        info.dwFlags = 0xFF
        if winmm.joyGetPosEx(target["index"], C.byref(info)) != 0:
            raise JoyProbeError("El mando PSP HID dejó de responder.")
        snapshot = decode_joyinfo(x=info.dwXpos, y=info.dwYpos,
                                  z=info.dwZpos, r=info.dwRpos,
                                  buttons=info.dwButtons, pov=info.dwPOV)
        x_range, y_range = ranges[target["index"]]
        return normalize_psp_joystick(buttons=snapshot.button_mask,
                                     pov=snapshot.pov, x=snapshot.axes[0],
                                     y=snapshot.axes[1], x_range=x_range,
                                     y_range=y_range)
