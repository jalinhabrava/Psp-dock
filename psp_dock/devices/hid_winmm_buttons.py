"""Read-only PSP Dock button test through Windows' joystick API.

Matches the VID/PID of the already validated PDK1 HID controller and
uses joyGetPosEx (the same WinMM game-controller input layer underlying
legacy joy.cpl). No writes, HID commands, device resets or USB ejects.
Useful when the HID control GET_REPORT(INPUT) request is unsupported.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass, asdict
import json
import os
import time

from psp_dock.devices.bridge_hid import VID, PID, MSC_TEST_PID, NATIVE_PID, probe as hid_probe


class JoyProbeError(ValueError):
    pass


_CAPS_UINTS = (
    "wXmin", "wXmax", "wYmin", "wYmax",
    "wZmin", "wZmax", "wNumButtons",
    "wPeriodMin", "wPeriodMax", "wRmin", "wRmax",
    "wUmin", "wUmax", "wVmin", "wVmax",
    "wCaps", "wMaxAxes", "wNumAxes", "wMaxButtons",
)


class JOYCAPSW(C.Structure):
    _fields_ = [
        ("wMid", W.WORD), ("wPid", W.WORD),
        ("szPname", W.WCHAR * 32),
        *((name, W.UINT) for name in _CAPS_UINTS),
        ("szRegKey", W.WCHAR * 32),
        ("szOEMVxD", W.WCHAR * 260),
    ]


class JOYINFOEX(C.Structure):
    _fields_ = [(name, W.DWORD) for name in (
        "dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos",
        "dwRpos", "dwUpos", "dwVpos", "dwButtons",
        "dwButtonNumber", "dwPOV", "dwReserved1", "dwReserved2",
    )]


@dataclass(frozen=True)
class JoySnapshot:
    axes: tuple[int, int, int, int]
    button_mask: int
    pressed: tuple[int, ...]
    pov: int


def matching_joystick_capabilities(candidates: list[dict], *, expected_product: int = PID) -> dict:
    """Never infer a physical PSP by name alone."""
    if type(expected_product) is not int or expected_product not in (PID, MSC_TEST_PID, NATIVE_PID):
        raise JoyProbeError('PID PSP Dock no reconocido.')
    matches = [item for item in candidates
               if item.get("vendor_id") == VID and item.get("product_id") == expected_product]
    if len(matches) != 1:
        raise JoyProbeError("WinMM no presenta una única PSP Type D con VID/PID esperados.")
    return matches[0]


def decode_joyinfo(*, x: int, y: int, z: int, r: int,
                   buttons: int, pov: int) -> JoySnapshot:
    if any(type(i) is not int for i in (x, y, z, r, buttons, pov)):
        raise JoyProbeError("Tipo de dato joystick inesperado.")
    if buttons < 0 or buttons > 0xFFFFFFFF:
        raise JoyProbeError("Máscara de botones fuera de rango.")
    return JoySnapshot(
        (x, y, z, r), buttons,
        tuple(i + 1 for i in range(32) if buttons & (1 << i)),
        pov,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="PSP Dock: botones WinMM solo lectura")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()
    if not 2 <= args.seconds <= 60:
        parser.error("Duración permitida 2-60 segundos.")
    try:
        if os.name != "nt":
            raise JoyProbeError("Solo disponible en Windows.")
        hid = hid_probe()
        if not hid.get("supported") or hid.get("errors") or len(hid.get("devices", [])) != 1:
            raise JoyProbeError("Falta una única sesión PDK1 HID verificada.")
        from dock import windows_devices
        if windows_devices():
            raise JoyProbeError("Storage PSP está montado; no probar joystick simultáneo.")
        winmm = C.WinDLL("winmm", use_last_error=True)
        winmm.joyGetNumDevs.argtypes = []
        winmm.joyGetNumDevs.restype = W.UINT
        winmm.joyGetDevCapsW.argtypes = [C.c_size_t, C.POINTER(JOYCAPSW), W.UINT]
        winmm.joyGetDevCapsW.restype = W.UINT
        winmm.joyGetPosEx.argtypes = [W.UINT, C.POINTER(JOYINFOEX)]
        winmm.joyGetPosEx.restype = W.UINT
        candidates = []
        for index in range(min(16, winmm.joyGetNumDevs())):
            caps = JOYCAPSW()
            if winmm.joyGetDevCapsW(index, C.byref(caps), C.sizeof(caps)) == 0:
                candidates.append({"index": index, "name": caps.szPname,
                                   "vendor_id": caps.wMid, "product_id": caps.wPid,
                                   "buttons": caps.wNumButtons})
        target = matching_joystick_capabilities(candidates)
        print("Joystick PSP Dock:", target["name"], "botones:", target["buttons"])
        print("Sueltos = button_mask 0. Pulsa uno a uno y mueve el analógico.")
        previous: JoySnapshot | None = None
        until = time.monotonic() + args.seconds
        while time.monotonic() < until:
            info = JOYINFOEX()
            info.dwSize = C.sizeof(info)
            info.dwFlags = 0xFF  # JOY_RETURNALL
            ret = winmm.joyGetPosEx(target["index"], C.byref(info))
            if ret:
                raise JoyProbeError("No se pudo leer el joystick; código WinMM " + str(ret))
            snapshot = decode_joyinfo(
                x=info.dwXpos, y=info.dwYpos, z=info.dwZpos, r=info.dwRpos,
                buttons=info.dwButtons, pov=info.dwPOV)
            if snapshot != previous:
                print(json.dumps(asdict(snapshot), ensure_ascii=False))
                previous = snapshot
            time.sleep(0.1)
        print("Terminado sin cambios de USB/PSP.")
        return 0
    except (JoyProbeError, OSError, RuntimeError) as exc:
        print("No se pudo completar la lectura WinMM:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
