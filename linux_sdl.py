"""Linux SDL2 controller profile for a session-bound native PSP Dock 0302.

Reads the kernel's joystick metadata; never grabs, detaches or writes a USB
interface. Settings are returned to the caller for its private PPSSPP profile
and child-process environment, never written into the user's global config.

The SDL key codes below are from PPSSPP v1.20.4:
https://github.com/hrydgard/ppsspp/blob/v1.20.4/SDL/SDLJoystick.cpp
https://github.com/hrydgard/ppsspp/blob/v1.20.4/Common/Input/InputState.h
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading

from native_dock import VID, PID, USB_REVISIONS
_SDL_INIT_JOYSTICK = 0x200
_SDL_LOCK = threading.Lock()


class LinuxControllerError(ValueError):
    pass


@dataclass(frozen=True)
class SDLPad:
    index: int
    guid: str
    path: str
    vendor: int
    product: int
    revision: int  # USB bcdDevice only; firmware identity comes from PDK2/v3.
    axes: int
    buttons: int
    hats: int


class _GUID(C.Structure):
    _fields_ = [("data", C.c_uint8 * 16)]


def _api(lib, name, result, *arguments):
    try:
        fn = getattr(lib, name)
    except AttributeError as exc:
        raise LinuxControllerError(
            "Se necesita SDL2 2.24 o posterior para identificar el mando por su ruta."
        ) from exc
    fn.restype, fn.argtypes = result, list(arguments)
    return fn


def enumerate_psp_pads() -> tuple[SDLPad, ...]:
    """Inspect native PSP joysticks through the system SDL2 library.

    SDL 2.24's device path is required so VID/PID alone cannot select a
    controller belonging to another physical USB device.
    """
    if os.environ.get("PSP_DOCK_TEST_NO_REAL_USB") == "1":
        raise LinuxControllerError("El acceso a mandos reales está desactivado en las pruebas.")
    try:
        lib = C.CDLL(ctypes.util.find_library("SDL2-2.0") or "libSDL2-2.0.so.0")
    except OSError as exc:
        raise LinuxControllerError("Instala la biblioteca SDL2 del sistema (2.24 o posterior).") from exc
    init = _api(lib, "SDL_InitSubSystem", C.c_int, C.c_uint32)
    quit_ = _api(lib, "SDL_QuitSubSystem", None, C.c_uint32)
    count = _api(lib, "SDL_NumJoysticks", C.c_int)
    vendor = _api(lib, "SDL_JoystickGetDeviceVendor", C.c_uint16, C.c_int)
    product = _api(lib, "SDL_JoystickGetDeviceProduct", C.c_uint16, C.c_int)
    revision = _api(lib, "SDL_JoystickGetDeviceProductVersion", C.c_uint16, C.c_int)
    guid = _api(lib, "SDL_JoystickGetDeviceGUID", _GUID, C.c_int)
    path = _api(lib, "SDL_JoystickPathForIndex", C.c_char_p, C.c_int)
    open_ = _api(lib, "SDL_JoystickOpen", C.c_void_p, C.c_int)
    close = _api(lib, "SDL_JoystickClose", None, C.c_void_p)
    axes = _api(lib, "SDL_JoystickNumAxes", C.c_int, C.c_void_p)
    buttons = _api(lib, "SDL_JoystickNumButtons", C.c_int, C.c_void_p)
    hats = _api(lib, "SDL_JoystickNumHats", C.c_int, C.c_void_p)
    lock = _api(lib, "SDL_LockJoysticks", None)
    unlock = _api(lib, "SDL_UnlockJoysticks", None)
    with _SDL_LOCK:
        if init(_SDL_INIT_JOYSTICK) != 0:
            raise LinuxControllerError("SDL2 no pudo abrir el subsistema de mandos.")
        try:
            lock()
            try:
                total = count()
                if total < 0 or total > 64:
                    raise LinuxControllerError("Enumeración de mandos SDL2 inválida.")
                found = []
                for index in range(total):
                    if vendor(index) != VID or product(index) != PID:
                        continue
                    device_path = path(index)
                    handle = open_(index)
                    if not device_path or not handle:
                        if handle:
                            close(handle)
                        raise LinuxControllerError("SDL2 no pudo identificar el mando PSP.")
                    try:
                        found.append(SDLPad(
                            index=index, guid=bytes(guid(index).data).hex(),
                            path=os.fsdecode(device_path), vendor=VID, product=PID,
                            revision=revision(index), axes=axes(handle),
                            buttons=buttons(handle), hats=hats(handle),
                        ))
                    finally:
                        close(handle)
                return tuple(found)
            finally:
                unlock()
        finally:
            quit_(_SDL_INIT_JOYSTICK)


def _usb_identity(parent: Path) -> tuple[int, int, int]:
    try:
        return tuple(int((parent / key).read_text(encoding="ascii").strip(), 16)
                     for key in ("idVendor", "idProduct", "bcdDevice"))
    except (OSError, ValueError) as exc:
        raise LinuxControllerError("No se puede verificar la identidad USB del mando.") from exc


def select_psp_pad(pads, usb_parent, *, sysfs_root=Path("/sys")) -> SDLPad:
    """Bind a unique SDL joystick to the USB parent already proved by PDK2."""
    root = Path(sysfs_root).resolve(strict=True)
    expected = Path(usb_parent).resolve(strict=True)
    if not expected.is_relative_to(root / "devices"):
        raise LinuxControllerError("El padre USB no está dentro del árbol sysfs esperado.")
    usb_identity = _usb_identity(expected)
    if usb_identity[:2] != (VID, PID) or usb_identity[2] not in USB_REVISIONS:
        raise LinuxControllerError("El padre USB no corresponde al Dock nativo. Su firmware debe confirmarse mediante PDK2/v3.")
    matches = []
    for pad in pads:
        if (pad.vendor, pad.product) != (VID, PID) or pad.revision not in USB_REVISIONS:
            continue
        if not re.fullmatch(r"/dev/input/event[0-9]+", pad.path):
            continue
        try:
            node = (root / "class/input" / Path(pad.path).name / "device").resolve(strict=True)
            parent = next(p for p in (node, *node.parents)
                          if (p / "idVendor").is_file() and (p / "idProduct").is_file())
        except (OSError, RuntimeError, StopIteration):
            continue
        if parent != expected or _usb_identity(parent) != usb_identity or pad.revision != usb_identity[2]:
            continue
        if ((pad.axes, pad.buttons, pad.hats) != (4, 12, 1)
                or type(pad.index) is not int or not 0 <= pad.index < 10
                or not re.fullmatch(r"[0-9a-f]{32}", pad.guid)
                or pad.guid == "0" * 32):
            raise LinuxControllerError("El descriptor o índice SDL2 del mando no es el esperado.")
        matches.append(pad)
    if len(matches) != 1:
        raise LinuxControllerError("Se necesita un único mando SDL2 de la misma PSP verificada.")
    return matches[0]


def controller_mapping(guid: str) -> str:
    """Map the firmware's 12-button FuSa layout to SDL's standard controller."""
    if not isinstance(guid, str) or not re.fullmatch(r"[0-9a-f]{32}", guid) or guid == "0" * 32:
        raise LinuxControllerError("GUID de SDL2 inválido.")
    return (
        guid + ",PSP Dock native 0302,"
        "a:b0,b:b1,x:b2,y:b3,leftshoulder:b4,rightshoulder:b5,"
        "back:b6,start:b7,guide:b8,"
        "dpup:h0.1,dpright:h0.2,dpdown:h0.4,dpleft:h0.8,"
        "leftx:a0,lefty:a1,platform:Linux,"
    )


def ppsspp_controls(index: int) -> bytes:
    """Private controls.ini for the SDL frontend, including HOME -> pause."""
    if type(index) is not int or not 0 <= index < 10:
        raise LinuxControllerError("Índice de mando PPSSPP inválido.")
    device = 10 + index
    # SDL shoulders are BUTTON_6/5, not the DirectInput BUTTON_5/6.
    bindings = {
        "Up": 19, "Down": 20, "Left": 21, "Right": 22,
        "Cross": 189, "Circle": 190, "Square": 191, "Triangle": 188,
        "L": 193, "R": 192, "Select": 196, "Start": 197,
        "An.Left": 4001, "An.Right": 4000, "An.Up": 4003, "An.Down": 4002,
        "Pause": 4,
    }
    lines = ["# PSP Dock managed Linux SDL2 profile", "[ControlMapping]"]
    for key, code in bindings.items():
        extra = ",1-111" if key == "Pause" else ""
        lines.append(f"{key} = {device}-{code}{extra}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def launch_profile(usb_parent, *, environ=None, pads=None, sysfs_root=Path("/sys")):
    """Return (child_environment, controls_bytes, selected_pad), without writes."""
    records = enumerate_psp_pads() if pads is None else pads
    pad = select_psp_pad(records, usb_parent, sysfs_root=sysfs_root)
    env = dict(os.environ if environ is None else environ)
    previous = env.get("SDL_GAMECONTROLLERCONFIG", "")
    preserved = [line for line in previous.splitlines()
                 if line.strip() and line.split(",", 1)[0].lower() != pad.guid]
    env["SDL_GAMECONTROLLERCONFIG"] = "\n".join([*preserved, controller_mapping(pad.guid)])
    return env, ppsspp_controls(pad.index), pad
