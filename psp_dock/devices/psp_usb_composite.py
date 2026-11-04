"""PC-only USB descriptor prototype for PSP Dock HID + vendor bulk.

Produces the raw STANDARD USB descriptors that a future audited PSP
device driver might advertise. This does NOT register a PSP USBBus
driver or alter hardware, and it does not implement Microsoft
OS descriptors / automatic WinUSB binding.

Windows usbccgp.sys could split the two interfaces; the PSP controller's
USB hardware and ARK USB Bus software must be verified to support this
configuration before any device-side build or physical use.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct


CONFIG_SIZE = 9 + 9 + 9 + 7 + 9 + 7 + 7
DEVICE_SIZE = 18
HID_INTERFACE = 0
BULK_INTERFACE = 1
INTERRUPT_IN_EP = 0x81
BULK_IN_EP = 0x82
BULK_OUT_EP = 0x03
PSP_HID_VID = 0x054C
PSP_EXISTING_HID_PID = 0x01CB


class DescriptorError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceDescriptors:
    device: bytes
    config: bytes
    high_speed: bool
    attested_hardware: bool = False
    winusb_bound: bool = False


def descriptor_set(*, high_speed: bool, report_length: int,
                   vid: int = PSP_HID_VID,
                   pid: int = PSP_EXISTING_HID_PID) -> DeviceDescriptors:
    if (type(high_speed) is not bool or
            type(report_length) is not int or not 1<=report_length<=2048 or
            type(vid) is not int or type(pid) is not int or
            not 1<=vid<=65535 or not 1<=pid<=65535):
        raise DescriptorError("Invalid USB descriptor arguments.")
    # bDeviceClass=0: interface classes selected independently.
    # Prototype reuses the existing PSP HID VID/PID for comparison only.
    # A redistributed product needs a legitimate VID/PID assignment.
    device = bytes((18, 1, 0, 2, 0, 0, 0, 64)) + struct.pack(
        "<HHHBBBB", vid, pid, 0x0105, 0, 1, 0, 1
    )
    config = struct.pack("<BBHBBBBB",
                         9, 2, CONFIG_SIZE, 2, 1, 0, 0xC0, 50)
    config += bytes((
        9, 4, HID_INTERFACE, 0, 1, 3, 0, 0, 0,     # HID joystick interface
        9, 0x21, 0x11, 0x01, 0, 1, 0x22,          # HID class/report descriptor
    )) + struct.pack("<H",report_length)
    config += struct.pack("<BBBBHB",
                          7, 5, INTERRUPT_IN_EP, 3, 8, 10)
    config += bytes((
        9, 4, BULK_INTERFACE, 0, 2, 0xFF, 0, 0, 0,  # custom vendor file interface
    ))
    bulk_packet = 512 if high_speed else 64
    config += struct.pack("<BBBBHB",7,5,BULK_IN_EP,2,bulk_packet,0)
    config += struct.pack("<BBBBHB",7,5,BULK_OUT_EP,2,bulk_packet,0)
    if len(device)!=DEVICE_SIZE or len(config)!=CONFIG_SIZE:
        raise DescriptorError("USB descriptor length inconsistency.")
    return DeviceDescriptors(device,config,high_speed)


def audit_configuration(data:bytes)->dict:
    """Validate all offsets, interfaces, class codes and endpoints."""
    if len(data)<9:
        raise DescriptorError("USB config header too short.")
    if (data[0]!=9 or data[1]!=2 or
            struct.unpack_from("<H",data,2)[0]!=len(data) or
            data[4]!=2):
        raise DescriptorError("USB total length or interface count mismatch.")
    offset=0
    interfaces=[]
    endpoints={}
    last_interface=None
    hid_report_len=None
    while offset<len(data):
        if offset+2>len(data):
            raise DescriptorError("USB truncated descriptor.")
        length,kind=data[offset],data[offset+1]
        if length<2 or offset+length>len(data):
            raise DescriptorError("USB invalid descriptor length.")
        buf=data[offset:offset+length]
        if kind==4:
            if length!=9 or buf[2]!=len(interfaces) or buf[3]!=0:
                raise DescriptorError("USB interface numbering invalid.")
            interfaces.append((buf[2],buf[5],buf[4]))
            endpoints[buf[2]]=[]
            last_interface=buf[2]
        elif kind==0x21:
            if length!=9 or last_interface!=HID_INTERFACE or buf[6]!=0x22:
                raise DescriptorError("HID class report layout invalid.")
            hid_report_len=struct.unpack_from("<H",buf,7)[0]
        elif kind==5:
            if length!=7 or last_interface is None:
                raise DescriptorError("Orphan USB endpoint.")
            address,attr,max_packet=buf[2],buf[3],struct.unpack_from("<H",buf,4)[0]
            endpoints[last_interface].append((address,attr,max_packet))
        offset+=length
    if interfaces != [(0,3,1),(1,0xFF,2)] or not hid_report_len:
        raise DescriptorError("USB HID + bulk interfaces are not present.")
    if endpoints[0]!=[(0x81,3,8)]:
        raise DescriptorError("PSP gamepad interrupt endpoint mismatch.")
    group=endpoints[1]
    if (len(group)!=2 or group[0][0]!=0x82 or group[1][0]!=0x03 or
            group[0][1]!=2 or group[1][1]!=2 or
            group[0][2]!=group[1][2] or
            group[0][2] not in (64,512)):
        raise DescriptorError("Vendor bulk endpoints mismatch.")
    addresses=[d[0] for rows in endpoints.values() for d in rows]
    if len(set(addresses))!=len(addresses):
        raise DescriptorError("USB endpoints collide.")
    return {
        "interfaces":2,"endpoints":3,"hid_interface":0,
        "bulk_interface":1,"high_speed":group[0][2]==512,
        "hid_report_length":hid_report_len,"bulk_packet_bytes":group[0][2],
        "physical_psp_verified":False,"winusb_bound":False,
    }
