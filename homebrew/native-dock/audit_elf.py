"""Audit 0306's actual MIPS ELF and USER PBP; never opens USB or a PSP.

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


def _cstring_at(elf, address: int) -> str:
    matches = [section for section in elf.sections if section[1] != 8 and
               section[2] & 2 and section[3] <= address < section[3] + section[5]]
    require(len(matches) == 1, "Dependency pointer is not unique initialized ELF data")
    section = matches[0]
    data = elf.section_bytes(section)[address - section[3]:address - section[3] + 128]
    require(b"\0" in data, "Unterminated dependency path/name")
    return data.split(b"\0", 1)[0].decode("ascii", "strict")


def hid_report_sizes(report: bytes) -> dict:
    """Count the actual compiled HID fields; no report IDs or long items."""
    position = report_size = report_count = 0
    bits = {"input": 0, "output": 0, "feature": 0}
    stack = []
    while position < len(report):
        prefix = report[position]
        position += 1
        require(prefix != 0xfe, "Long HID items are outside this reviewed descriptor")
        length = (0, 1, 2, 4)[prefix & 3]
        require(position + length <= len(report), "Truncated HID item")
        value = int.from_bytes(report[position:position+length], "little")
        position += length
        item_type, tag = (prefix >> 2) & 3, prefix >> 4
        if item_type == 1:
            if tag == 7:
                report_size = value
            elif tag == 9:
                report_count = value
            elif tag == 8:
                raise ValueError("Native reports must keep report ID zero")
            elif tag == 10:
                stack.append((report_size, report_count))
            elif tag == 11:
                require(bool(stack), "Invalid HID global stack")
                report_size, report_count = stack.pop()
        elif item_type == 0 and tag in (8, 9, 11):
            require(report_size > 0 and report_count > 0, "Empty HID field")
            bits[{8: "input", 9: "output", 11: "feature"}[tag]] += report_size * report_count
    require(not stack and all(n % 8 == 0 for n in bits.values()), "Unbalanced/unaligned HID reports")
    return {name: count // 8 for name, count in bits.items()}


def pbp_title(raw: bytes) -> str:
    """Read PARAM.SFO's TITLE, not an incidental byte string in the ELF."""
    require(len(raw) >= 40 and raw[:4] == b"\0PBP", "Invalid PBP header")
    begin, end = struct.unpack_from("<II", raw, 8)
    require(40 <= begin <= end <= len(raw), "Invalid PARAM.SFO bounds")
    sfo = raw[begin:end]
    require(len(sfo) >= 20 and sfo[:4] == b"\0PSF", "Invalid PARAM.SFO")
    keys, data, count = struct.unpack_from("<III", sfo, 8)
    require(count <= 128 and 20 + 16 * count <= keys <= data <= len(sfo), "Invalid SFO table")
    titles = []
    for index in range(count):
        key_offset, fmt, length, capacity, offset = struct.unpack_from("<HHIII", sfo, 20 + 16 * index)
        require(keys + key_offset < data, "SFO key outside table")
        key_tail = sfo[keys+key_offset:data]
        require(b"\0" in key_tail, "Unterminated SFO key")
        if key_tail.split(b"\0", 1)[0] != b"TITLE":
            continue
        require(fmt == 0x0204 and 0 < length <= capacity and data + offset + capacity <= len(sfo),
                "Invalid SFO TITLE entry")
        value = sfo[data+offset:data+offset+length]
        require(value[-1:] == b"\0" and b"\0" not in value[:-1], "Invalid SFO TITLE text")
        titles.append(value[:-1].decode("utf-8", "strict"))
    require(len(titles) == 1, "SFO needs exactly one TITLE")
    return titles[0]


def _compiled_protocol(elf) -> tuple[bytes, dict]:
    """Derive the host contract from the exact compiled protocol symbols."""
    # A short literal can become instruction immediates under MIPS -O2.
    # Inspect the symbols used by encoder AND validator, not arbitrary substrings.
    wire_prefix = elf.data("pdn_wire_prefix", 5)
    require(elf.symbols["pdn_wire_prefix"][1] == 5, "Compiled wire prefix symbol has the wrong width")
    require(wire_prefix == b"PDK2\x03", "Compiled wire prefix must be exactly PDK2/v3")
    identity = elf.data("pdn_identity", 12)
    require(elf.symbols["pdn_identity"][1] == 12, "Compiled firmware identity symbol has the wrong width")
    require(identity == struct.pack("<4sHHI", b"NAT3", 0x0306, 0x000F, 0x03060001),
            "Compiled firmware identity differs from reviewed PDK2/v3 build")
    _, firmware_revision, capabilities, build_id = struct.unpack("<4sHHI", identity)
    protocol_abi = elf.data("pdn_protocol_abi", 17)
    require(elf.symbols["pdn_protocol_abi"][1] == 17, "Compiled protocol policy symbol has the wrong width")
    require(protocol_abi == bytes.fromhex("50444e4103400804010203043f01010005"),
            "Compiled protocol policy differs from explicit host/progress/shutdown contract")
    require(protocol_abi[4] == wire_prefix[4], "Wire version differs from compiled protocol ABI")
    transport_abi = elf.data("pdn_transport_abi", 10)
    require(elf.symbols["pdn_transport_abi"][1] == 10 and
            transport_abi == b"PDF4" + bytes((32, 2, 0xc0, 64, 0xe0, 16)),
            "Fragmented Output4 command contract or CRC16 commit differs")
    return protocol_abi, {
        "revision": f"{firmware_revision:04X}",
        "protocol": wire_prefix[:4].decode("ascii") + "/v" + str(wire_prefix[4]),
        "firmware_identity_in_protocol": True,
        "host_begin_required": bool(protocol_abi[13]),
        "host_progress_supported": bool(protocol_abi[14]),
        "host_shutdown_supported": bool(capabilities & 0x0008 and protocol_abi[12] & 0x20
                                        and protocol_abi[16] == 5),
        "usb_revision_is_firmware_revision": bool(protocol_abi[15]),
        "firmware_build_id": f"{build_id:08X}", "protocol_capabilities": f"{capabilities:04X}",
        "command_transport": "output4-fragments-crc16",
    }


def audit(raw: bytes) -> dict:
    elf = MipsElf(raw)
    require(elf.module_attributes() & 0x1000, "Native USB controller must be kernel PRX")
    module_info = elf.section_bytes(elf.section_names[".rodata.sceModuleInfo"])
    require(module_info[2:4] == b"\x06\x03", "Expected kernel module version 3.6")
    required_modules = (
        ("flash0:/kd/semawm.prx", "sceSemawm"),
        ("flash0:/kd/usbstor.prx", "sceUSB_Stor_Driver"),
        ("flash0:/kd/usbstormgr.prx", "sceUSB_Stor_Mgr_Driver"),
        ("flash0:/kd/usbstorms.prx", "sceUSB_Stor_Ms_Driver"),
    )
    dependencies = elf.data("dependencies", 4 * 20)
    for index, expected in enumerate(required_modules):
        path, name, uid, owned, started = struct.unpack_from("<IIiII", dependencies, index * 20)
        require((_cstring_at(elf, path), _cstring_at(elf, name)) == expected,
                "Native dependency order/name differs from reviewed 0306 bootstrap")
        require((uid, owned, started) == (-1, 0, 0), "Dependency ownership must start empty")
    for forbidden_path in (b"usbstorboot.prx", b"pcact.prx", b"npdrm.prx", b"usbstoreflash.prx", b"usbdevice.prx"):
        require(forbidden_path not in raw, "Forbidden optional/redirection module in 0306: " + forbidden_path.decode())
    require(elf.data("pdn_interface", 12) == struct.pack("<iii", -1, 0, 1),
            "HID must request one automatically assigned interface")
    require(elf.data("pdn_endpoints", 24) == struct.pack("<6I", 0, 0, 0, 1, 0, 0),
            "HID must own exactly its local EP0 and interrupt IN endpoint")
    for speed, interval in (("hi", 6), ("full", 4)):
        device = elf.data("pdn_device_" + speed, 20)
        require(device[:8] == b"\x12\x01\x00\x02\0\0\0\x40", "Invalid USB 2.0 device/EP0")
        require(struct.unpack_from("<H", device, 12)[0] == 0x0306 and device[17] == 1,
                "Wrong 0306 descriptor revision/configuration")
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
    require(report.endswith(b"\x75\x08\x95\x40\xb1\x02\xc0"), "Expected 64-byte native FEATURE")
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
    protocol_abi, protocol_policy = _compiled_protocol(elf)
    report_sizes = hid_report_sizes(report)
    require(report_sizes == {"input": 8, "output": 4, "feature": 64},
            "Compiled HID report sizes disagree with PDK2/v3 identity")
    require(protocol_abi[5:8] == bytes((report_sizes["feature"], report_sizes["input"], report_sizes["output"])),
            "Descriptor and compiled ABI sizes disagree")
    return {
        **protocol_policy, "elf_machine": "MIPS", "kernel_module_verified": True,
        "mandatory_modules": [path for path, _ in required_modules], "optional_bootstrap_modules": [],
        "function_descriptors_verified": True, "hid_function_interfaces": 1,
        "hid_input_bytes": report_sizes["input"], "hid_output_bytes": report_sizes["output"],
        "hid_feature_bytes": report_sizes["feature"],
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
    require(pbp_title(pbp) == "PSP Dock", "XMB title must be exactly PSP Dock")
    user_elf = MipsElf(args.user_elf.read_bytes())
    user_info = user_elf.section_bytes(user_elf.section_names[".rodata.sceModuleInfo"])
    require(user_info[2:4] == b"\x06\x03", "Expected USER launcher module version 3.6")
    result["xmb_title"] = "PSP Dock"
    result["pbp_sha256"] = hashlib.sha256(pbp).hexdigest()
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
