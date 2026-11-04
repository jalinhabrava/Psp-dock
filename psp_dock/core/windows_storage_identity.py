"""Fresh, read-only volume -> disk association without a PowerShell process.

Device numbers are compared only within this call, never persisted as device
identity. Sony USB ancestry and card serial are still checked by psp_usb.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import contextmanager
import os
import re
import uuid

class GUID(C.Structure):
    _fields_ = [('Data1',C.c_uint32),('Data2',C.c_uint16),('Data3',C.c_uint16),('Data4',C.c_ubyte*8)]


class SP_DEVICE_INTERFACE_DATA(C.Structure):
    _fields_ = [('cbSize',C.c_uint32),('ClassGuid',GUID),('Flags',C.c_uint32),('Reserved',C.c_void_p)]


class SP_DEVINFO_DATA(C.Structure):
    _fields_ = [('cbSize',W.DWORD),('ClassGuid',GUID),('DevInst',W.DWORD),('Reserved',C.c_void_p)]


class STORAGE_DEVICE_NUMBER(C.Structure):
    _fields_ = [('DeviceType',W.DWORD),('DeviceNumber',W.DWORD),('PartitionNumber',W.DWORD)]


DISK_GUID = GUID.from_buffer_copy(uuid.UUID('53f56307-b6bf-11d0-94f2-00a0c91efb8b').bytes_le)


class DiskApi:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('La identidad de disco nativa requiere Windows.')
        self.k = C.WinDLL('kernel32',use_last_error=True)
        self.s = C.WinDLL('setupapi',use_last_error=True)
        signatures = [
            (self.k,'CreateFileW',[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE],W.HANDLE),
            (self.k,'CloseHandle',[W.HANDLE],W.BOOL),
            (self.k,'DeviceIoControl',[W.HANDLE,W.DWORD,C.c_void_p,W.DWORD,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.c_void_p],W.BOOL),
            (self.s,'SetupDiGetClassDevsW',[C.POINTER(GUID),W.LPCWSTR,W.HWND,W.DWORD],W.HANDLE),
            (self.s,'SetupDiEnumDeviceInterfaces',[W.HANDLE,C.c_void_p,C.POINTER(GUID),W.DWORD,C.POINTER(SP_DEVICE_INTERFACE_DATA)],W.BOOL),
            (self.s,'SetupDiGetDeviceInterfaceDetailW',[W.HANDLE,C.POINTER(SP_DEVICE_INTERFACE_DATA),C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.POINTER(SP_DEVINFO_DATA)],W.BOOL),
            (self.s,'SetupDiGetDeviceInstanceIdW',[W.HANDLE,C.POINTER(SP_DEVINFO_DATA),W.LPWSTR,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),
            (self.s,'SetupDiGetDeviceRegistryPropertyW',[W.HANDLE,C.POINTER(SP_DEVINFO_DATA),W.DWORD,C.POINTER(W.DWORD),C.c_void_p,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),
            (self.s,'SetupDiDestroyDeviceInfoList',[W.HANDLE],W.BOOL),
        ]
        for dll,name,args,result in signatures:
            fn=getattr(dll,name);fn.argtypes=args;fn.restype=result

    @contextmanager
    def open(self,path):
        handle=self.k.CreateFileW(path,0,3,None,3,0,None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        try:yield handle
        finally:self.k.CloseHandle(handle)

    def number(self,handle):
        value=STORAGE_DEVICE_NUMBER();received=W.DWORD()
        if not self.k.DeviceIoControl(handle,0x002D1080,None,0,C.byref(value),C.sizeof(value),C.byref(received),None):
            raise C.WinError(C.get_last_error())
        if received.value != C.sizeof(value) or value.DeviceType != 7:
            raise ValueError('El volumen no ofrece un número de disco válido.')
        return int(value.DeviceType),int(value.DeviceNumber)

    def disks(self):
        device_set=self.s.SetupDiGetClassDevsW(C.byref(DISK_GUID),None,None,0x12)
        if device_set == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        try:
            for index in range(256):
                interface=SP_DEVICE_INTERFACE_DATA();interface.cbSize=C.sizeof(interface)
                if not self.s.SetupDiEnumDeviceInterfaces(device_set,None,C.byref(DISK_GUID),index,C.byref(interface)):
                    if C.get_last_error()==259:return
                    raise C.WinError(C.get_last_error())
                needed=W.DWORD();dev=SP_DEVINFO_DATA();dev.cbSize=C.sizeof(dev)
                self.s.SetupDiGetDeviceInterfaceDetailW(device_set,C.byref(interface),None,0,C.byref(needed),None)
                if not 6<=needed.value<=65536:
                    raise ValueError('Longitud de interfaz de disco no válida.')
                detail=C.create_string_buffer(needed.value)
                C.cast(detail,C.POINTER(W.DWORD))[0]=8 if C.sizeof(C.c_void_p)==8 else 6
                if not self.s.SetupDiGetDeviceInterfaceDetailW(device_set,C.byref(interface),detail,len(detail),None,C.byref(dev)):
                    raise C.WinError(C.get_last_error())
                path=C.wstring_at(C.addressof(detail)+4)
                instance=C.create_unicode_buffer(1024)
                if not self.s.SetupDiGetDeviceInstanceIdW(device_set,C.byref(dev),instance,len(instance),None):
                    raise C.WinError(C.get_last_error())
                if not re.match(r'^USBSTOR\\DISK&VEN_SONY&PROD_',instance.value,re.I):
                    continue
                model=''
                for property_id in (12,0):
                    text=C.create_unicode_buffer(1024);kind=W.DWORD()
                    if self.s.SetupDiGetDeviceRegistryPropertyW(device_set,C.byref(dev),property_id,C.byref(kind),text,C.sizeof(text),None) and kind.value==1:
                        model=text.value;break
                yield path,instance.value,model
            raise ValueError('Demasiadas interfaces de disco.')
        finally:self.s.SetupDiDestroyDeviceInfoList(device_set)


def physical_disk(drive, *, api=None):
    if not isinstance(drive,str) or not re.fullmatch(r'[A-Z]:\\',drive,re.I):
        raise ValueError('Se requiere una raíz de unidad.')
    api=api or DiskApi()
    with api.open('\\\\.\\'+drive[:2]) as volume:
        before=api.number(volume)
        matches=[]
        for path,instance,model in api.disks():
            with api.open(path) as disk:
                if api.number(disk)==before:
                    matches.append((instance,model))
        if api.number(volume)!=before or len(matches)!=1:
            raise ValueError('El volumen cambió o no tiene un disco Sony único.')
        return matches[0]
