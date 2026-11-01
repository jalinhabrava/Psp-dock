"""Audit the compiled synthetic USBSTOR + HID diagnostic and its USER EBOOT.

Checks actual little-endian MIPS ELF descriptor pointers and linked symbols.
Also checks the generated FAT12 reference image and exact embedded XMB assets.
Does not discover, mount, open, or modify any PSP/USB/Memory Stick device.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


class ElfAuditError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ElfAuditError(message)


class MipsElf:
    def __init__(self, raw: bytes):
        self.raw = raw
        require(52 <= len(raw) <= 8 * 1024 * 1024, "ELF length invalid")
        header = struct.unpack_from("<16sHHIIIIIHHHHHH", raw)
        require(header[0][:7] == b"\x7fELF\x01\x01\x01" and header[2] == 8,
                "Expected little-endian ELF32 MIPS")
        start, size, count = header[6], header[11], header[12]
        require(size == 40 and 1 <= count <= 512 and start + count * size <= len(raw),
                "Invalid ELF sections")
        self.sections = [struct.unpack_from("<10I", raw, start + i * size) for i in range(count)]
        self.symbols: dict[str, tuple[int, int, int]] = {}
        self.all_symbols: dict[str, tuple[int, int, int]] = {}
        self.section_names: dict[str, tuple[int, ...]] = {}
        require(header[13] < count, "Invalid section-name table index")
        names = self.section_bytes(self.sections[header[13]])
        for section in self.sections:
            self.section_names[self.string(names, section[0])] = section
            if section[1] != 2:
                continue
            require(section[9] == 16 and section[6] < count and section[5] % 16 == 0,
                    "Invalid symbol table")
            strings = self.section_bytes(self.sections[section[6]])
            data = self.section_bytes(section)
            for offset in range(0, len(data), 16):
                name, value, length, _info, _other, shndx = struct.unpack_from("<IIIBBH", data, offset)
                text = self.string(strings, name)
                if text:
                    self.all_symbols[text] = (value, length, shndx)
                    if 0 < shndx < count:
                        self.symbols[text] = (value, length, shndx)

    @staticmethod
    def string(table: bytes, start: int) -> str:
        require(start < len(table), "Invalid ELF string offset")
        end = table.find(b"\0", start)
        require(end >= start, "Unterminated ELF string")
        try:
            return table[start:end].decode("ascii", "strict")
        except UnicodeDecodeError as exc:
            raise ElfAuditError("Non-ASCII ELF symbol/section name") from exc

    def section_bytes(self, section) -> bytes:
        start, size = section[4], section[5]
        require(start + size <= len(self.raw), "ELF section is truncated")
        return self.raw[start:start + size]

    def address(self, symbol: str) -> int:
        require(symbol in self.symbols, "Missing compiled symbol: " + symbol)
        return self.symbols[symbol][0]

    def file_offset(self, symbol: str) -> int:
        self.address(symbol)
        address, length, number = self.symbols[symbol]
        section = self.sections[number]
        require(section[1] != 8 and address >= section[3] and address + length <= section[3] + section[5],
                "Symbol is not initialized data: " + symbol)
        return section[4] + address - section[3]

    def data(self, symbol: str, size: int) -> bytes:
        require(self.symbols.get(symbol, (None, None))[1] == size,
                "Wrong compiled object size: " + symbol)
        start = self.file_offset(symbol)
        return self.raw[start:start + size]

    def module_attributes(self) -> int:
        section = self.section_names.get(".rodata.sceModuleInfo")
        require(section is not None, "Missing PSP module-info section")
        data = self.section_bytes(section)
        require(len(data) >= 4, "Truncated PSP module info")
        return struct.unpack_from("<H", data)[0]


def audit(raw: bytes) -> dict:
    elf = MipsElf(raw)
    # Both names preserve the same ABI. This permits a direct rename of the
    # already-reviewed table symbols without weakening any byte-level check.
    prefix = "pdc_" if "pdc_interface" in elf.symbols else "msc_"
    for speed, packet, interval in (("hi", 512, 6), ("full", 64, 4)):
        interfaces = prefix + "interfaces_" + speed
        endpoints = prefix + "endpoints_" + speed
        settings = prefix + "settings_" + speed
        device = elf.data(prefix + "device_" + speed, 20)
        require(device[:4] == b"\x12\x01\x00\x02", "Expected USB 2.0 device descriptor")
        require(device[4:8] == b"\0\0\0\x40" and device[17] == 1,
                "Composite parent class, EP0 size or configuration count invalid")
        require(struct.unpack_from("<H", device, 12)[0] == 0x0206,
                "Wrong synthetic MSC diagnostic revision; expected 0206")
        table = struct.unpack("<6I", elf.data(settings, 24))
        require(table == (elf.address(interfaces), 0, 1, elf.address(interfaces) + 24, 0, 1),
                "Expected two separate 12-byte interface-settings records")
        config_name = prefix + "configdesc_" + speed
        config = elf.data(config_name, 24)
        require(config[:6] == b"\x09\x02\x39\x00\x02\x01", "MSC+HID configuration header invalid")
        require(config[7] == 0xc0, "Synthetic diagnostic must be self-powered")
        require(struct.unpack_from("<I", config, 12)[0] == elf.address(settings),
                "Configuration points to wrong settings")
        require(struct.unpack_from("<II", config, 16) == (0, 0), "Unexpected configuration extra bytes")
        internal = struct.unpack("<4I", elf.data(prefix + "config_" + speed, 16))
        require(internal == (elf.address(config_name), elf.address(settings), elf.address(interfaces), elf.address(endpoints)),
                "Internal configuration pointer chain invalid")
        desc = elf.data(interfaces, 72)
        require(desc[:9] == bytes((9, 4, 0, 0, 1, 3, 0, 0, 1)), "HID interface changed")
        require(desc[24:33] == bytes((9, 4, 1, 0, 2, 8, 6, 0x50, 1)),
                "Expected genuine SCSI transparent/Bulk-Only USB mass-storage interface")
        require(struct.unpack_from("<I", desc, 12)[0] == elf.address(endpoints), "HID endpoint pointer invalid")
        require(struct.unpack_from("<I", desc, 36)[0] == elf.address(endpoints) + 16, "MSC endpoint pointer invalid")
        require(struct.unpack_from("<II", desc, 16) == (elf.address(prefix + "hid_descriptor"), 9),
                "HID extra descriptor missing")
        require(struct.unpack_from("<II", desc, 40) == (0, 0), "Unexpected MSC extra descriptor")
        require(desc[48:] == bytes(24), "Interface sentinel corrupted")
        eps = elf.data(endpoints, 64)
        expected = ((0x81, 3, 8, interval), (0x82, 2, packet, 0), (0x03, 2, packet, 0))
        for i, (address, kind, mps, polling) in enumerate(expected):
            require(eps[i*16:i*16+7] == struct.pack("<BBBBHB", 7, 5, address, kind, mps, polling),
                    "Endpoint direction/type/packet/polling interval invalid")
            require(eps[i*16+8:i*16+16] == bytes(8), "Unexpected endpoint extra data")
        require(eps[48:] == bytes(16), "Endpoint sentinel corrupted")
        for symbol in (interfaces, endpoints, prefix + "device_" + speed):
            require(elf.address(symbol) % 64 == 0, "Descriptor array lacks cache-line alignment")
    require(elf.data(prefix + "interface", 12) == struct.pack("<iii", -1, 0, 2),
            "Driver must own exactly two interfaces")
    require(struct.unpack("<12I", elf.data(prefix + "endpoints", 48)) ==
            (0, 0, 0, 1, 0, 0, 2, 0, 0, 3, 0, 0), "USB endpoint numbering invalid")
    io_symbols = sorted(name for name in elf.all_symbols if re.search(r"(^|_)sceIo", name) or "IoFileMgr" in name)
    require(not io_symbols, "Synthetic kernel includes forbidden filesystem imports/symbols: " + ", ".join(io_symbols))
    require(not any("sceUsbstorBoot" in name for name in elf.all_symbols), "Unexpected Sony storage bootstrap")
    for symbol in ("msc_init", "msc_begin", "msc_read_sector", "msc_read_data", "msc_data_complete", "msc_csw",
                   "sceUsbbdRegister", "sceUsbbdReqRecv", "sceUsbbdReqSend", "sceUsbbdStall", "sceUsbActivate"):
        elf.address(symbol)
    require(elf.module_attributes() & 0x1000, "USB driver is not a kernel module")
    return {"elf_machine": "MIPS", "revision": "0206", "interfaces": ["HID", "USBSTOR-SCSI-BOT"],
            "descriptor_tables_verified": True, "kernel_filesystem_imports": [],
            "expected_usb_identity": {"vid": "054C", "pid": "01CC", "wire_identity_verified": False},
            "hid_poll_interval_ms": {"high_speed": 4, "full_speed": 4},
            "physical_enumeration_verified": False, "real_memory_stick_access": False}


def audit_user(raw: bytes, object_raw: bytes) -> dict:
    elf, obj = MipsElf(raw), MipsElf(object_raw)
    all_names = set(elf.all_symbols)
    forbidden_final = sorted(name for name in all_names if re.search(r"(^|_)sceUsb", name) or "ForKernel" in name)
    require(not forbidden_final, "USER EBOOT has privileged USB/kernel imports: " + ", ".join(forbidden_final))
    direct = sorted(name for name, item in obj.all_symbols.items() if item[2] == 0)
    privileged = {"sceKernelCpuSuspendIntr", "sceKernelCpuResumeIntr", "sceKernelLoadModuleWithApitype2",
                  "sceKernelLoadModuleForLoadExecVSHMs2", "sceKernelLoadModuleForLoadExecVSHMs1"}
    bad_direct = [name for name in direct if re.search(r"(^|_)sceUsb", name) or "ForKernel" in name
                  or name in privileged or re.search(r"(^|_)sceIo(Write|Remove|Rename|Devctl|Mkdir|Rmdir|Format)$", name)]
    require(not bad_direct, "USER app directly calls privileged USB/kernel/file mutation API: " + ", ".join(bad_direct))
    require(not (elf.module_attributes() & 0x1000), "Launcher must be a USER module")
    return {"user_module_verified": True, "direct_object_imports": direct,
            "privileged_usb_or_kernel_imports": []}


def audit_pbp(raw: bytes, assets: Path) -> dict:
    require(len(raw) >= 40 and raw[:4] == b"\0PBP", "Invalid EBOOT.PBP")
    offsets = struct.unpack_from("<8I", raw, 8)
    require(offsets[0] >= 40 and list(offsets) == sorted(offsets) and offsets[-1] <= len(raw),
            "Invalid PBP section offsets")
    names = ((1, "ICON0.PNG", (144, 80)), (3, "PIC0.PNG", (310, 180)), (4, "PIC1.PNG", (480, 272)))
    result = {}
    for index, filename, expected_size in names:
        data = (assets / filename).read_bytes()
        require(len(data) >= 33 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR",
                "Invalid PNG asset: " + filename)
        size = struct.unpack_from(">II", data, 16)
        require(size == expected_size, "Wrong PNG dimensions: " + filename)
        embedded = raw[offsets[index]:offsets[index+1]]
        require(embedded == data, "PBP asset differs from exact reviewed source: " + filename)
        result[filename] = {"dimensions": list(size), "sha256": hashlib.sha256(data).hexdigest(),
                            "embedded_exactly": True}
    return result


def audit_image(image: bytes) -> dict:
    require(len(image) == 1024 * 512, "Synthetic disk size invalid")
    require(image[510:512] == b"\x55\xaa", "Missing FAT boot signature")
    require(struct.unpack_from("<H", image, 11)[0] == 512 and image[13] == 1,
            "Unexpected FAT sector/cluster size")
    reserved = struct.unpack_from("<H", image, 14)[0]
    fats, root_count = image[16], struct.unpack_from("<H", image, 17)[0]
    fat_size = struct.unpack_from("<H", image, 22)[0]
    require((reserved, fats, root_count, fat_size) == (1, 2, 32, 3), "Unexpected FAT12 geometry")
    require(image[43:54] == b"PSPDOCKTEST" and struct.unpack_from("<I", image, 39)[0] == 0x50444334,
            "Wrong synthetic volume label/serial")
    fat = image[512:2048]
    require(fat == image[2048:3584], "FAT copies differ")
    root_lba, data_lba = 7, 9
    files = {}
    for offset in (32, 64):
        entry = image[root_lba*512+offset:root_lba*512+offset+32]
        cluster, length = struct.unpack_from("<H", entry, 26)[0], struct.unpack_from("<I", entry, 28)[0]
        name, seen, content = entry[:11].decode("ascii"), set(), bytearray()
        while cluster < 0xff8:
            require(2 <= cluster < 1017 and cluster not in seen, "Invalid FAT chain")
            seen.add(cluster)
            position = (data_lba + cluster - 2) * 512
            content.extend(image[position:position+512])
            position = cluster + cluster // 2
            pair = int.from_bytes(fat[position:position+2], "little")
            cluster = pair >> 4 if cluster & 1 else pair & 0xfff
        require(length <= len(content) and length > len(content)-512, "FAT chain/file size disagree")
        content = bytes(content[:length])
        if name == "PROBE   DAT":
            require(length == 150003 and len(seen) == 293, "Wrong probe file size/chain")
            require(hashlib.sha256(content).hexdigest() ==
                    "e2d2ed8fba5bff4fc1e3e2cf9fd4508a6e3fd8f2d8429d44196c6b7ec8ba001e", "Probe SHA-256 mismatch")
        elif name == "SESSION BIN":
            require(content == bytes(range(1, 17)) and len(seen) == 1, "Reference session nonce mismatch")
        else:
            raise ElfAuditError("Unexpected synthetic root entry")
        files[name] = {"size": length, "clusters": len(seen), "sha256": hashlib.sha256(content).hexdigest()}
    require(set(files) == {"PROBE   DAT", "SESSION BIN"}, "Missing synthetic files")
    return {"bytes": len(image), "sha256": hashlib.sha256(image).hexdigest(), "files": files,
            "generated_reference_only": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("--user-elf", type=Path)
    parser.add_argument("--user-object", type=Path)
    parser.add_argument("--pbp", type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.elf.read_bytes())
    if args.user_elf or args.user_object:
        require(args.user_elf is not None and args.user_object is not None, "Both USER ELF/object are required")
        result["user_app"] = audit_user(args.user_elf.read_bytes(), args.user_object.read_bytes())
    if args.pbp or args.assets:
        require(args.pbp is not None and args.assets is not None, "Both PBP/assets are required")
        result["xmb_assets"] = audit_pbp(args.pbp.read_bytes(), args.assets)
    if args.image:
        result["synthetic_disk"] = audit_image(args.image.read_bytes())
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
