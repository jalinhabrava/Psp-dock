"""Shared read-only ELF32/MIPS parser used to audit native PSP Dock 0308.

Moved from the legacy synthetic USB diagnostic verifier without changing its
validation logic. This contains only the ELF parser, not the obsolete 0207
firmware audit. Never opens USB devices or writes PSP files.
"""
from __future__ import annotations
import struct
import re
import hashlib
from pathlib import Path

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


# USER ELF and PBP resource integrity checks are shared by the final build.

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
