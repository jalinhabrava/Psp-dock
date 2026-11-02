"""Audit 0300's actual MIPS ELF and USER PBP; never opens USB or a PSP.

The default device descriptor chosen by Sony's composed USB bus is a hardware
question. This audit proves our descriptor and imports, not that composition
has enumerated or that any observed Windows volume is a Memory Stick.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct

_baseline = Path(__file__).resolve().parent.parent / "composite-mscdemo" / "audit_elf.py"
_spec = importlib.util.spec_from_file_location("pspdock_elf_reader", _baseline)
assert _spec is not None and _spec.loader is not None
_reader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reader)
MipsElf, require = _reader.MipsElf, _reader.require


def audit(raw: bytes) -> dict:
    elf = MipsElf(raw)
    require(elf.module_attributes() & 0x1000, "Native USB controller must be kernel PRX")
    require(elf.data("pdn_interface", 12) == struct.pack("<iii", -1, 0, 1),
            "HID must request one automatically assigned interface")
    require(elf.data("pdn_endpoints", 24) == struct.pack("<6I", 0, 0, 0, 1, 0, 0),
            "HID must own exactly its local EP0 and interrupt IN endpoint")
    for speed, interval in (("hi", 6), ("full", 4)):
        device = elf.data("pdn_device_" + speed, 20)
        require(device[:8] == b"\x12\x01\x00\x02\0\0\0\x40", "Invalid USB 2.0 device/EP0")
        require(struct.unpack_from("<H", device, 12)[0] == 0x0300 and device[17] == 1,
                "Wrong 0300 descriptor revision/configuration")
        interfaces, endpoints = "pdn_interfaces_" + speed, "pdn_endpoint_" + speed
        settings, config_name = "pdn_settings_" + speed, "pdn_configdesc_" + speed
        require(elf.data(settings, 12) == struct.pack("<3I", elf.address(interfaces), 0, 1),
                "Invalid padded interface settings")
        config = elf.data(config_name, 24)
        require(config[:6] == b"\x09\x02\x22\x00\x01\x01" and config[7] == 0xc0,
                "This function owns one HID interface; Sony supplies its own storage function")
        require(struct.unpack_from("<III", config, 12) == (elf.address(settings), 0, 0),
                "Invalid configuration pointer chain")
        require(elf.data("pdn_config_" + speed, 16) == struct.pack("<4I", elf.address(config_name),
                elf.address(settings), elf.address(interfaces), elf.address(endpoints)),
                "Invalid internal configuration")
        descriptor = elf.data(interfaces, 48)
        require(descriptor[:9] == bytes((9, 4, 0, 0, 1, 3, 0, 0, 1)), "HID descriptor changed")
        require(struct.unpack_from("<III", descriptor, 12) ==
                (elf.address(endpoints), elf.address("pdn_hid_descriptor"), 9), "Invalid HID extra/endpoint")
        require(descriptor[24:] == bytes(24), "Interface sentinel corrupted")
        ep = elf.data(endpoints, 32)
        require(ep[:7] == struct.pack("<BBBBHB", 7, 5, 0x81, 3, 8, interval),
                "HID endpoint/interval changed")
        require(ep[8:] == bytes(24), "Endpoint metadata/sentinel corrupted")
        for name in (interfaces, endpoints, "pdn_device_" + speed):
            require(elf.address(name) % 64 == 0, "Descriptor cache alignment missing: " + name)
    report = elf.data("report_descriptor", elf.symbols["report_descriptor"][1])
    require(report.endswith(b"\x75\x08\x95\x20\xb1\x02\xc0"), "Expected 32-byte FEATURE")
    hid = elf.data("pdn_hid_descriptor", 9)
    require(hid[:7] == bytes((9, 0x21, 0x10, 1, 0, 1, 0x22)) and
            struct.unpack_from("<H", hid, 7)[0] == len(report), "HID report length mismatch")
    forbidden = sorted(name for name in elf.all_symbols if re.search(r"(^|_)sceIo", name)
                       or "IoFileMgr" in name or "sceUsbstorBoot" in name or "pspUsbDevice" in name)
    require(not forbidden, "Forbidden filesystem/early storage bootstrap/redirection import: " + ", ".join(forbidden))
    for name in ("sceUsbActivate", "sceUsbDeactivate", "sceUsbStart", "sceUsbStop",
                 "sceUsbbdRegister", "sceUsbbdUnregister", "sceUsbbdReqSend", "sceUsbbdReqRecv",
                 "sceUsbbdReqCancelAll", "sceKernelLoadModule", "sceKernelStartModule",
                 "sceKernelStopModule", "sceKernelUnloadModule", "sceKernelFindModuleByName"):
        elf.address(name)
    require(b"PDK2" in raw and b"PDK1" not in raw, "Native controller must use separate PDK2 protocol")
    return {
        "revision": "0300", "elf_machine": "MIPS", "kernel_module_verified": True,
        "function_descriptors_verified": True, "hid_function_interfaces": 1,
        "hid_input_bytes": 8, "hid_feature_bytes": 32, "protocol": "PDK2/v2",
        "storage_backend": "native Sony USBStor_Driver", "kernel_filesystem_imports": [],
        "early_sony_boot_imports": [], "expected_pid": "01CD",
        "wire_identity_verified": False, "sony_composition_physically_verified": False,
        "real_memory_stick_access_physically_verified": False,
        "safe_phase_is_windows_eject_proof": False,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("--user-elf", required=True, type=Path)
    parser.add_argument("--user-object", required=True, type=Path)
    parser.add_argument("--pbp", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.elf.read_bytes())
    result["user_app"] = _reader.audit_user(args.user_elf.read_bytes(), args.user_object.read_bytes())
    pbp = args.pbp.read_bytes()
    result["xmb_assets"] = _reader.audit_pbp(pbp, args.assets)
    require(b"PSP Dock - Memory Stick + Mando 0300\0" in pbp, "Missing branded 0300 XMB title")
    result["pbp_sha256"] = hashlib.sha256(pbp).hexdigest()
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
