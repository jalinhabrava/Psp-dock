"""Experimental WinUSB transport for the separate PSP composite RO driver.

No driver installer, registry changes, USBSTOR commands or device resets.
Opening requires the exact currently enumerated PSP vendor-interface path,
then validates device/interface/pipe descriptors and the firmware response.
Only joystick-independent READ/LIST traffic is implemented.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os
import struct
import threading
from typing import Callable

from psp_winusb_probe import candidate_paths, expected_path
from psp_bulk_protocol import Frame, BulkProtocolError, HEADER, CRC, MAX_PAYLOAD

COMMAND_CELL = 512
RESPONSE_CELL = 65536
FIRMWARE_REVISION = 0x0203
STATUS_REQUEST = 0x53
CAPABILITIES_RO = 7


class WinUSBError(OSError):
    pass


@dataclass(frozen=True)
class FirmwareStatus:
    session: bytes
    active: bool
    connected: bool
    commands: int


def parse_firmware_status(raw: bytes) -> FirmwareStatus:
    if not isinstance(raw, bytes) or len(raw) != 32:
        raise WinUSBError("Respuesta PDC2 incompleta.")
    magic, version, caps, active, connected, session, count, error = struct.unpack(
        "<4sBBBB16sIi", raw)
    if (magic != b"PDC2" or version != 2 or caps != CAPABILITIES_RO or
            active != 1 or connected != 1 or error != 0 or not any(session)):
        raise WinUSBError("El dispositivo no es el controlador de lectura esperado.")
    return FirmwareStatus(session, True, True, count)


class USBInterface(C.Structure):
    _pack_ = 1
    _fields_ = [(name, C.c_ubyte) for name in (
        "length", "type", "number", "alternate", "endpoints", "usb_class",
        "subclass", "protocol", "string_index")]


class PipeInfo(C.Structure):
    _fields_ = [("kind", C.c_uint32), ("pipe", C.c_ubyte),
                ("max_packet", C.c_uint16), ("interval", C.c_ubyte)]


class SetupPacket(C.Structure):
    _pack_ = 1
    _fields_ = [("request_type", C.c_ubyte), ("request", C.c_ubyte),
                ("value", C.c_uint16), ("index", C.c_uint16),
                ("length", C.c_uint16)]


class WindowsBackend:
    """Thin, inspectable ctypes bindings; imported DLLs only on Windows."""

    hardware = True

    def __init__(self, timeout_ms: int = 5000):
        if os.name != "nt":
            raise WinUSBError("WinUSB requiere Windows.")
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 30000:
            raise WinUSBError("Timeout WinUSB fuera de límites.")
        self.timeout_ms = timeout_ms
        self.file = None
        self.handle = C.c_void_p()
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.usb = C.WinDLL("winusb", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD,
            C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        self.kernel.CreateFileW.restype = W.HANDLE
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.CloseHandle.restype = W.BOOL
        signatures = {
            "WinUsb_Initialize": [W.HANDLE, C.POINTER(C.c_void_p)],
            "WinUsb_Free": [C.c_void_p],
            "WinUsb_GetDescriptor": [C.c_void_p, C.c_ubyte, C.c_ubyte, C.c_uint16,
                C.c_void_p, W.ULONG, C.POINTER(W.ULONG)],
            "WinUsb_QueryInterfaceSettings": [C.c_void_p, C.c_ubyte, C.POINTER(USBInterface)],
            "WinUsb_QueryPipe": [C.c_void_p, C.c_ubyte, C.c_ubyte, C.POINTER(PipeInfo)],
            "WinUsb_SetPipePolicy": [C.c_void_p, C.c_ubyte, W.ULONG, W.ULONG, C.c_void_p],
            "WinUsb_ControlTransfer": [C.c_void_p, SetupPacket, C.c_void_p,
                W.ULONG, C.POINTER(W.ULONG), C.c_void_p],
            "WinUsb_ReadPipe": [C.c_void_p, C.c_ubyte, C.c_void_p,
                W.ULONG, C.POINTER(W.ULONG), C.c_void_p],
            "WinUsb_WritePipe": [C.c_void_p, C.c_ubyte, C.c_void_p,
                W.ULONG, C.POINTER(W.ULONG), C.c_void_p],
        }
        for name, arguments in signatures.items():
            method = getattr(self.usb, name)
            method.argtypes = arguments
            method.restype = W.BOOL

    def check(self, ok, name: str) -> None:
        if not ok:
            raise WinUSBError(C.get_last_error(), "WinUSB: " + name)

    def open(self, path: str) -> None:
        # WinUSB requires FILE_FLAG_OVERLAPPED even for synchronous transfers.
        # Commands have one sequence and one owner. Do not let a second
        # process open a parallel WinUSB command stream on this interface.
        self.file = self.kernel.CreateFileW(path, 0xC0000000, 0, None, 3,
                                            0x40000000, None)
        if self.file in (None, C.c_void_p(-1).value):
            self.file = None
            raise WinUSBError(C.get_last_error(), "No se puede abrir la interfaz PSP.")
        try:
            self.check(self.usb.WinUsb_Initialize(self.file, C.byref(self.handle)), "Initialize")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.handle.value:
            self.usb.WinUsb_Free(self.handle)
            self.handle = C.c_void_p()
        if self.file is not None:
            self.kernel.CloseHandle(self.file)
            self.file = None

    def descriptors(self) -> dict:
        raw = C.create_string_buffer(18)
        count = W.ULONG()
        self.check(self.usb.WinUsb_GetDescriptor(self.handle, 1, 0, 0,
            raw, 18, C.byref(count)), "GetDescriptor")
        if count.value != 18:
            raise WinUSBError("Descriptor de dispositivo USB incompleto.")
        wire = raw.raw
        if wire[:2] != b'\x12\x01':
            raise WinUSBError("No se recibió un descriptor de dispositivo USB válido.")
        vid, pid, revision = struct.unpack_from("<HHH", wire, 8)
        interface = USBInterface()
        self.check(self.usb.WinUsb_QueryInterfaceSettings(
            self.handle, 0, C.byref(interface)), "QueryInterfaceSettings")
        if interface.length != 9 or interface.type != 4:
            raise WinUSBError("Descriptor de interfaz USB no reconocido.")
        pipes = []
        if interface.endpoints > 16:
            raise WinUSBError("Demasiados endpoints USB.")
        for index in range(interface.endpoints):
            pipe = PipeInfo()
            self.check(self.usb.WinUsb_QueryPipe(self.handle, 0, index, C.byref(pipe)), "QueryPipe")
            pipes.append((int(pipe.pipe), int(pipe.kind), int(pipe.max_packet)))
        return {"vid": vid, "pid": pid, "revision": revision,
                "device_class": wire[4], "configurations": wire[17],
                "interface": int(interface.number), "alternate": int(interface.alternate),
                "class": int(interface.usb_class), "subclass": int(interface.subclass),
                "protocol": int(interface.protocol), "pipes": pipes}

    def set_timeouts(self) -> None:
        value = W.ULONG(self.timeout_ms)
        for pipe in (0x82, 0x03):
            self.check(self.usb.WinUsb_SetPipePolicy(self.handle, pipe, 3,
                C.sizeof(value), C.byref(value)), "PIPE_TRANSFER_TIMEOUT")

    def status(self) -> bytes:
        setup = SetupPacket(0xC1, STATUS_REQUEST, 0, 1, 32)
        buf = C.create_string_buffer(32)
        count = W.ULONG()
        self.check(self.usb.WinUsb_ControlTransfer(self.handle, setup, buf, 32,
            C.byref(count), None), "PDC2_STATUS")
        if count.value != 32:
            raise WinUSBError("Estado del controlador incompleto.")
        return buf.raw

    def write(self, payload: bytes) -> int:
        buf = C.create_string_buffer(payload, len(payload))
        count = W.ULONG()
        self.check(self.usb.WinUsb_WritePipe(self.handle, 0x03, buf, len(payload),
            C.byref(count), None), "WritePipe")
        return int(count.value)

    def read(self, size: int) -> bytes:
        buf = C.create_string_buffer(size)
        count = W.ULONG()
        self.check(self.usb.WinUsb_ReadPipe(self.handle, 0x82, buf, size,
            C.byref(count), None), "ReadPipe")
        if count.value > size:
            raise WinUSBError("Tamaño USB imposible.")
        return buf.raw[:count.value]


def validate_device(info: dict) -> None:
    expected = {"vid": 0x054C, "pid": 0x01CB, "revision": FIRMWARE_REVISION,
                "device_class": 0, "configurations": 1, "interface": 1,
                "alternate": 0, "class": 0xFF, "subclass": 0x50, "protocol": 2}
    mismatch = [f"{key}: visto={info.get(key)!r}, esperado={value!r}"
                for key, value in expected.items() if info.get(key) != value]
    if mismatch:
        raise WinUSBError("Descriptor PSP compuesto distinto: " + '; '.join(mismatch))
    pipes = sorted(info.get("pipes", []))
    if (len(pipes) != 2 or pipes[0][:2] != (0x03, 2) or pipes[1][:2] != (0x82, 2)
            or pipes[0][2] != pipes[1][2] or pipes[0][2] not in (64, 512)):
        raise WinUSBError("No están los dos endpoints BULK esperados.")


class WinUSBTransport:
    """One in-flight command/response; ambiguous errors poison the session."""

    def __init__(self, path: str, *, backend=None, enumerate_paths: Callable = candidate_paths):
        self._lock = threading.RLock()
        self._closed = True
        self._poisoned = False
        if not expected_path(path) or path not in enumerate_paths():
            raise WinUSBError("Interfaz no enumerada bajo el GUID de PSP Dock.")
        self.backend = backend or WindowsBackend()
        self.path = path
        try:
            self.backend.open(path)
            self._closed = False
            self.descriptor = self.backend.descriptors()
            validate_device(self.descriptor)
            self.backend.set_timeouts()
            self.firmware = parse_firmware_status(self.backend.status())
        except Exception:
            self.backend.close()
            self._closed = True
            raise

    def exchange(self, request: Frame) -> Frame:
        with self._lock:
            if self._closed or self._poisoned:
                raise WinUSBError("Sesión USB cerrada o interrumpida; no reintentar a ciegas.")
            if request.session != self.firmware.session:
                raise WinUSBError("Sesión del paquete distinta al firmware.")
            packet = request.encode()
            if len(packet) > COMMAND_CELL:
                raise WinUSBError("Comando USB fuera de límites.")
            try:
                if self.backend.write(packet.ljust(COMMAND_CELL, b"\0")) != COMMAND_CELL:
                    raise WinUSBError("Escritura parcial del comando USB.")
                wire = self.backend.read(RESPONSE_CELL)
                if len(wire) != RESPONSE_CELL:
                    raise WinUSBError("Respuesta USB corta o desconectada.")
                length = struct.unpack_from("<I", wire, 36)[0]
                if length > MAX_PAYLOAD:
                    raise WinUSBError("Longitud de respuesta fuera de límites.")
                end = HEADER.size + CRC.size + length
                if any(wire[end:]):
                    raise WinUSBError("Padding USB no nulo.")
                response = Frame.decode(wire[:end])
                if response.session != request.session or response.seq != request.seq:
                    raise WinUSBError("Respuesta USB de otra sesión o secuencia.")
                return response
            except (OSError, ValueError, struct.error):
                self._poisoned = True
                raise

    def check_session(self) -> None:
        with self._lock:
            if self._closed or self._poisoned:
                raise WinUSBError("La sesión dejó de ser utilizable.")
            try:
                if parse_firmware_status(self.backend.status()).session != self.firmware.session:
                    raise WinUSBError("La PSP se reconectó durante la copia.")
            except (OSError, ValueError, struct.error):
                self._poisoned = True
                raise

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.backend.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
