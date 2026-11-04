"""Windows probe for the PSP Dock v1 HID feature protocol.

Enumeration only reads candidates with VID 054c / PID 01cb, HID usage 01:04,
and a 33-byte feature buffer. A valid PDK1 reply is required before mutation.
This module never ejects volumes, launches software or changes USB drivers.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
from dataclasses import asdict, dataclass
import json
import os
import re
import secrets
import struct
from contextlib import contextmanager

VID, PID = 0x054C, 0x01CB
MSC_TEST_PID = 0x01CC
NATIVE_PID = 0x01CD  # Separate PDK2 transport; not accepted by the PDK1 command API.
MAGIC = b"PDK1"
VERSION = 1
STATUS, REQUEST_STORAGE = 1, 2
MODE_HID = 2
FEATURE_SIZE = 32
WINDOWS_REPORT_SIZE = FEATURE_SIZE + 1
FLAG_CONNECTED, FLAG_PENDING, FLAG_CONSUMED = 1, 2, 4
_FRAME = struct.Struct("<4sBBBB16sIi")
_EXPECTED_PATH = re.compile(r"^\\\\\?\\hid#vid_054c&pid_01cb(?:&|#)", re.IGNORECASE)


def expected_product_pattern(expected_product: int = PID):
    """Allow the stable controller or the explicitly selected MSC diagnostic."""
    if type(expected_product) is not int or expected_product not in (PID, MSC_TEST_PID):
        raise BridgeHidError('PID PSP Dock no reconocido.')
    return re.compile(r'^\\\\\?\\hid#vid_054c&pid_' + f'{expected_product:04x}' + r'(?:&|#)', re.I)


class BridgeHidError(RuntimeError):
    """Transport, identity or protocol validation failed."""


@dataclass(frozen=True)
class BridgeStatus:
    session: str
    sequence: int
    result: int
    connected: bool
    pending: bool
    consumed: bool
    mode: int = MODE_HID
    version: int = VERSION


@dataclass(frozen=True)
class BridgeDevice:
    path: str
    status: BridgeStatus
    vendor_id: int = VID
    product_id: int = PID
    revision: int | None = None


def _session_bytes(session: str | bytes) -> bytes:
    try:
        value = bytes.fromhex(session) if isinstance(session, str) else bytes(session)
    except (ValueError, TypeError) as exc:
        raise BridgeHidError("La sesión no es un identificador hexadecimal válido") from exc
    if len(value) != 16 or not any(value):
        raise BridgeHidError("La sesión debe contener 16 bytes y no ser cero")
    return value


def decode_status(report: bytes) -> BridgeStatus:
    """Decode the Windows HID buffer, including its required zero report ID."""
    if len(report) != WINDOWS_REPORT_SIZE or report[0] != 0:
        raise BridgeHidError("Longitud o ID de informe HID inesperado")
    magic, version, op, mode, flags, session, seq, result = _FRAME.unpack(report[1:])
    if magic != MAGIC or version != VERSION or op != STATUS or mode != MODE_HID:
        raise BridgeHidError("El dispositivo no ofrece PSP Dock HID v1 activo")
    if flags & ~7 or not any(session):
        raise BridgeHidError("Estado o sesión de PSP Dock no válido")
    pending, consumed = bool(flags & FLAG_PENDING), bool(flags & FLAG_CONSUMED)
    if (pending and consumed) or (pending and (not seq or result != 1)) or (
        consumed and (not seq or result != 2)
    ) or (not pending and not consumed and (seq or result)):
        raise BridgeHidError("Estado de comando PSP Dock incoherente")
    return BridgeStatus(session.hex(), seq, result, bool(flags & FLAG_CONNECTED), pending, consumed)


def encode_storage_request(session: str | bytes, sequence: int) -> bytes:
    if not isinstance(sequence, int) or isinstance(sequence, bool) or not 0 < sequence <= 0xFFFFFFFF:
        raise BridgeHidError("La secuencia debe ser un entero de 32 bits distinto de cero")
    return b"\0" + _FRAME.pack(MAGIC, VERSION, REQUEST_STORAGE, 0, 0,
                                 _session_bytes(session), sequence, 0)


class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD),
                ("Data4", C.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", W.DWORD), ("Reserved", C.c_void_p)]


class HIDD_ATTRIBUTES(C.Structure):
    _fields_ = [("Size", W.ULONG), ("VendorID", W.USHORT),
                ("ProductID", W.USHORT), ("VersionNumber", W.USHORT)]


class HIDP_CAPS(C.Structure):
    _fields_ = [(name, W.USHORT) for name in (
        "Usage", "UsagePage", "InputReportByteLength", "OutputReportByteLength",
        "FeatureReportByteLength"
    )] + [("Reserved", W.USHORT * 17)] + [(name, W.USHORT) for name in (
        "NumberLinkCollectionNodes", "NumberInputButtonCaps", "NumberInputValueCaps",
        "NumberInputDataIndices", "NumberOutputButtonCaps", "NumberOutputValueCaps",
        "NumberOutputDataIndices", "NumberFeatureButtonCaps", "NumberFeatureValueCaps",
        "NumberFeatureDataIndices"
    )]


class _WindowsHid:
    def __init__(self, *, expected_product: int = PID) -> None:
        self.expected_product = expected_product
        self.expected_path = expected_product_pattern(expected_product)
        if os.name != "nt":
            raise BridgeHidError("La consulta HID nativa requiere Windows")
        self.hid = C.WinDLL("hid", use_last_error=True)
        self.setup = C.WinDLL("setupapi", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        signatures = [
            (self.hid, "HidD_GetHidGuid", [C.POINTER(GUID)], None),
            (self.hid, "HidD_GetAttributes", [W.HANDLE, C.POINTER(HIDD_ATTRIBUTES)], C.c_ubyte),
            (self.hid, "HidD_GetPreparsedData", [W.HANDLE, C.POINTER(C.c_void_p)], C.c_ubyte),
            (self.hid, "HidD_FreePreparsedData", [C.c_void_p], C.c_ubyte),
            (self.hid, "HidP_GetCaps", [C.c_void_p, C.POINTER(HIDP_CAPS)], W.LONG),
            (self.hid, "HidD_GetFeature", [W.HANDLE, C.c_void_p, W.ULONG], C.c_ubyte),
            (self.hid, "HidD_SetFeature", [W.HANDLE, C.c_void_p, W.ULONG], C.c_ubyte),
            (self.setup, "SetupDiGetClassDevsW", [C.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD], W.HANDLE),
            (self.setup, "SetupDiEnumDeviceInterfaces", [W.HANDLE, C.c_void_p, C.POINTER(GUID),
                W.DWORD, C.POINTER(SP_DEVICE_INTERFACE_DATA)], W.BOOL),
            (self.setup, "SetupDiGetDeviceInterfaceDetailW", [W.HANDLE, C.POINTER(SP_DEVICE_INTERFACE_DATA),
                C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL),
            (self.setup, "SetupDiDestroyDeviceInfoList", [W.HANDLE], W.BOOL),
            (self.kernel, "CreateFileW", [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            (self.kernel, "CloseHandle", [W.HANDLE], W.BOOL),
        ]
        for lib, name, args, result in signatures:
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result

    @staticmethod
    def _failure(action: str) -> BridgeHidError:
        return BridgeHidError(f"{action}: {C.WinError(C.get_last_error())}")

    def paths(self) -> list[str]:
        guid = GUID()
        self.hid.HidD_GetHidGuid(C.byref(guid))
        devices = self.setup.SetupDiGetClassDevsW(C.byref(guid), None, None, 0x12)
        if devices == C.c_void_p(-1).value:
            raise self._failure("Enumerar dispositivos HID")
        paths = []
        try:
            index = 0
            while True:
                interface = SP_DEVICE_INTERFACE_DATA()
                interface.cbSize = C.sizeof(interface)
                if not self.setup.SetupDiEnumDeviceInterfaces(devices, None, C.byref(guid), index, C.byref(interface)):
                    if C.get_last_error() == 259:
                        break
                    raise self._failure("Enumerar interfaces HID")
                index += 1
                required = W.DWORD()
                self.setup.SetupDiGetDeviceInterfaceDetailW(devices, C.byref(interface), None, 0,
                                                          C.byref(required), None)
                if C.get_last_error() != 122 or required.value < 6:
                    raise self._failure("Medir ruta HID")
                detail = C.create_string_buffer(required.value)
                # Win32 Unicode detail.cbSize=6; Win64=8. DevicePath offset=4 on both.
                C.cast(detail, C.POINTER(W.DWORD))[0] = 8 if C.sizeof(C.c_void_p) == 8 else 6
                if not self.setup.SetupDiGetDeviceInterfaceDetailW(devices, C.byref(interface), detail,
                        required.value, None, None):
                    raise self._failure("Leer ruta HID")
                path = C.wstring_at(C.addressof(detail) + 4)
                if self.expected_path.match(path):
                    paths.append(path)
        finally:
            self.setup.SetupDiDestroyDeviceInfoList(devices)
        return paths

    @contextmanager
    def open(self, path: str, write: bool = False):
        if not self.expected_path.match(path):
            raise BridgeHidError("La ruta no corresponde al VID/PID HID seleccionado")
        if write and self.expected_product != PID:
            raise BridgeHidError('El diagnostico MSC solo admite consultas HID.')
        handle = self.kernel.CreateFileW(path, 0xC0000000 if write else 0, 3, None, 3, 0, None)
        if handle == C.c_void_p(-1).value:
            raise self._failure("Abrir PSP HID")
        try:
            self.attributes(handle)
            data = C.c_void_p()
            if not self.hid.HidD_GetPreparsedData(handle, C.byref(data)):
                raise self._failure("Leer descriptor HID")
            try:
                caps = HIDP_CAPS()
                if self.hid.HidP_GetCaps(data, C.byref(caps)) != 0x00110000:
                    raise BridgeHidError("HidP_GetCaps rechazó el descriptor")
                if (caps.UsagePage, caps.Usage, caps.FeatureReportByteLength,
                    caps.InputReportByteLength, caps.OutputReportByteLength) != (1, 4, 33, 9, 5):
                    raise BridgeHidError("El descriptor no corresponde a PSP Dock HID v1")
            finally:
                self.hid.HidD_FreePreparsedData(data)
            yield handle
        finally:
            self.kernel.CloseHandle(handle)

    def attributes(self, handle) -> dict:
        """Read the actual device revision, without inferring it from PID."""
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = C.sizeof(attrs)
        if not self.hid.HidD_GetAttributes(handle, C.byref(attrs)):
            raise self._failure("Leer identidad HID")
        if (attrs.VendorID, attrs.ProductID) != (VID, self.expected_product):
            raise BridgeHidError("VID/PID de HID inesperados")
        return {"vendor_id": int(attrs.VendorID),
                "product_id": int(attrs.ProductID),
                "revision": int(attrs.VersionNumber)}

    def get(self, handle) -> BridgeStatus:
        buf = C.create_string_buffer(WINDOWS_REPORT_SIZE)
        if not self.hid.HidD_GetFeature(handle, buf, len(buf)):
            raise self._failure("HidD_GetFeature")
        return decode_status(buf.raw)

    def set(self, handle, report: bytes) -> None:
        if self.expected_product != PID:
            raise BridgeHidError('El diagnostico MSC no admite ordenes HID de retorno.')
        if len(report) != WINDOWS_REPORT_SIZE:
            raise BridgeHidError("Longitud de comando no válida")
        buf = C.create_string_buffer(report, len(report))
        if not self.hid.HidD_SetFeature(handle, buf, len(buf)):
            raise self._failure("HidD_SetFeature; entrega no confirmada, comprobar estado USB")


def probe(*, expected_product: int = PID) -> dict:
    """Read-only diagnostic. Rejected candidates are errors, never bridges."""
    expected_product_pattern(expected_product)
    if os.name != "nt":
        return {"supported": False, "devices": [], "errors": ["HID nativo requiere Windows"]}
    api = _WindowsHid() if expected_product == PID else _WindowsHid(expected_product=expected_product)
    devices, errors = [], []
    for path in api.paths():
        try:
            with api.open(path) as handle:
                attributes = api.attributes(handle)
                devices.append(asdict(BridgeDevice(path, api.get(handle), **attributes)))
        except (BridgeHidError, OSError) as exc:
            errors.append({"path": path, "error": str(exc)})
    return {"supported": True, "devices": devices, "errors": errors}


def request_storage(path: str, session: str | bytes, sequence: int | None = None) -> dict:
    """Send once to a verified bridge in the expected session.

    A successful call means Windows completed SET_FEATURE, not that storage
    mounted. The caller MUST wait for the expected storage device separately.
    If transport fails, delivery is uncertain; never infer a safe mode change.
    """
    expected = _session_bytes(session).hex()
    sequence = (secrets.randbits(32) or 1) if sequence is None else sequence
    report = encode_storage_request(expected, sequence)
    api = _WindowsHid()
    with api.open(path, write=True) as handle:
        status = api.get(handle)  # mandatory identity+session check, on same handle
        if status.session != expected:
            raise BridgeHidError("La sesión HID cambió; se cancela la orden antigua")
        if not status.connected:
            raise BridgeHidError("HID aún no está conectado")
        if status.pending or status.consumed:
            if status.sequence != sequence:
                raise BridgeHidError("Esta sesión ya tiene una orden de retorno")
            return {"sent": False, "already_accepted": True, "session": expected,
                    "sequence": sequence, "storage_confirmed": False}
        api.set(handle, report)
    return {"sent": True, "already_accepted": False, "session": expected,
            "sequence": sequence, "storage_confirmed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sondeo de solo lectura del puente PSP Dock HID")
    parser.parse_args()
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
