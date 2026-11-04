"""Read-only Windows discovery for a FUTURE PSP Dock vendor bulk interface.

No WinUSB device currently exists on the user's PSP: the installed
HID-only driver exposes only the known joystick interface. This module
does NOT install a driver, bind an INF, send bulk packets, eject USB,
register PnP triggers or modify Windows settings.

The GUID is a prototype DeviceInterfaceGUID intended for Microsoft OS
descriptors in a future composite HID + vendor-bulk PSP driver.
Changing USB VID/PID, GUID or interface topology requires updating all
correlated host proofs and a fresh disposable-media test.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import re
import uuid


INTERFACE_GUID = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/jalinhabrava/Psp-dock/usb-composite/vendor-file-v1",
)
VID, PID, INTERFACE_NUMBER = 0x054c,0x01cb,1
EXPECTED_PIPE_TYPES = ((0x82,2),(0x03,2))


class WinUsbPrototypeError(ValueError):
    pass


class GUID(C.Structure):
    _fields_ = [
        ("Data1",W.DWORD),("Data2",W.WORD),
        ("Data3",W.WORD),("Data4",C.c_ubyte*8),
    ]


class DeviceInterface(C.Structure):
    _fields_ = [
        ("cbSize",W.DWORD),
        ("InterfaceClassGuid",GUID),
        ("Flags",W.DWORD),
        ("Reserved",C.c_void_p),
    ]


def expected_path(path:str)->bool:
    """Reject unrelated interfaces even if a future OS GUID is spoofed."""
    return bool(isinstance(path,str) and re.fullmatch(
        r"\\\\\?\\usb#vid_054c&pid_01cb&mi_01#[^\\]{8,512}",
        path,re.IGNORECASE,
    ))


def parse_interface_info(*, vendor_id:int, product_id:int,
                         interface:int, class_code:int,
                         endpoints:list[tuple[int,int,int]]) -> dict:
    if (type(vendor_id) is not int or vendor_id!=VID or
            type(product_id) is not int or product_id!=PID or
            interface!=INTERFACE_NUMBER or class_code!=0xff):
        raise WinUsbPrototypeError('Dispositivo diferente del PSP Dock compuesto esperado.')
    if (not isinstance(endpoints,list) or len(endpoints)!=2 or
            any(type(v) is not int for row in endpoints for v in row) or
            [(addr,kind) for addr,kind,_ in endpoints]!=list(EXPECTED_PIPE_TYPES)
            or endpoints[0][2]!=endpoints[1][2] or
            endpoints[0][2] not in (64,512)):
        raise WinUsbPrototypeError('Endpoints BULK incorrectos; no leer archivos.')
    return {'vid':VID,'pid':PID,
            'interface':INTERFACE_NUMBER,
            'high_speed':endpoints[0][2]==512,
            'bulk_max_packet':endpoints[0][2],
            'known_gamepad_hid_coexistence':False,
            'actual_fat_ownership_verified':False,
            'bulk_transport_ready':False}


def _interface_guid()->GUID:
    value=INTERFACE_GUID
    return GUID(value.time_low,value.time_mid,value.time_hi_version,
                (C.c_ubyte*8).from_buffer_copy(value.bytes[8:]))


def candidate_paths()->list[str]:
    """List ONLY matching future interface GUIDs through Windows SetupAPI."""
    if os.name!='nt':
        return []
    api=C.WinDLL("setupapi",use_last_error=True)
    api.SetupDiGetClassDevsW.argtypes=[
        C.POINTER(GUID),W.LPCWSTR,W.HWND,W.DWORD]
    api.SetupDiGetClassDevsW.restype=W.HANDLE
    api.SetupDiEnumDeviceInterfaces.argtypes=[
        W.HANDLE,C.c_void_p,C.POINTER(GUID),W.DWORD,
        C.POINTER(DeviceInterface)]
    api.SetupDiEnumDeviceInterfaces.restype=W.BOOL
    api.SetupDiGetDeviceInterfaceDetailW.argtypes=[
        W.HANDLE,C.POINTER(DeviceInterface),C.c_void_p,W.DWORD,
        C.POINTER(W.DWORD),C.c_void_p]
    api.SetupDiGetDeviceInterfaceDetailW.restype=W.BOOL
    api.SetupDiDestroyDeviceInfoList.argtypes=[W.HANDLE]
    api.SetupDiDestroyDeviceInfoList.restype=W.BOOL
    guid=_interface_guid()
    flags=0x10|0x02
    result=api.SetupDiGetClassDevsW(C.byref(guid),None,None,flags)
    if result in (None,C.c_void_p(-1).value):
        raise WinUsbPrototypeError('No se puede consultar el GUID PSP Dock BULK.')
    devices=[]
    try:
        for index in range(64):
            iface=DeviceInterface()
            iface.cbSize=C.sizeof(iface)
            if not api.SetupDiEnumDeviceInterfaces(
                    result,None,C.byref(guid),index,C.byref(iface)):
                if C.get_last_error()==259:  # ERROR_NO_MORE_ITEMS
                    return devices
                raise WinUsbPrototypeError('Error al enumerar interfaces USB futuras.')
            length=W.DWORD()
            api.SetupDiGetDeviceInterfaceDetailW(
                result,C.byref(iface),None,0,C.byref(length),None)
            if C.get_last_error()!=122 or not 10<=length.value<=4096:
                raise WinUsbPrototypeError('Tamaño de ruta Windows USB inválido.')
            buffer=C.create_string_buffer(length.value)
            C.cast(buffer,C.POINTER(W.DWORD))[0]=8 if C.sizeof(C.c_void_p)==8 else 6
            if not api.SetupDiGetDeviceInterfaceDetailW(
                    result,C.byref(iface),buffer,length.value,None,None):
                raise WinUsbPrototypeError('No se pudo leer ruta de USB BULK.')
            path=C.wstring_at(C.addressof(buffer)+4)
            if expected_path(path):
                devices.append(path)
        raise WinUsbPrototypeError('Demasiadas interfaces registradas.')
    finally:
        api.SetupDiDestroyDeviceInfoList(result)


def probe()->dict:
    """No USB transactions. Presence does not prove a usable file channel."""
    if os.name!='nt':
        return {'supported':False,'interface_guid':str(INTERFACE_GUID),
                'candidate_paths':[],'bulk_ready':False}
    paths=candidate_paths()
    return {'supported':True,'interface_guid':str(INTERFACE_GUID),
            'candidate_paths':paths,'bulk_ready':False,
            'note':'Solo descubrimiento: faltan descriptores y driver WinUSB.'}
