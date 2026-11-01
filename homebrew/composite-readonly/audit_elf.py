"""Verify the actual MIPS ELF descriptor tables, not merely source text.

The Sony ABI is a per-interface {descriptor, alternate, count} array.
This catches the tempting but wrong {pointer0, pointer1, count=2} layout
even when every C translation unit compiles successfully.
No third-party Python packages or PSP/USB access are required.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct


class ElfAuditError(ValueError):
    pass


class MipsElf:
    def __init__(self, raw: bytes):
        self.raw = raw
        if not 52 <= len(raw) <= 4 * 1024 * 1024:
            raise ElfAuditError('ELF length invalid')
        header = struct.unpack_from('<16sHHIIIIIHHHHHH', raw)
        if header[0][:7] != b'\x7fELF\x01\x01\x01' or header[2] != 8:
            raise ElfAuditError('Expected little-endian ELF32 MIPS')
        start, size, count = header[6], header[11], header[12]
        if size != 40 or not 1 <= count <= 512 or start + count * size > len(raw):
            raise ElfAuditError('Invalid ELF sections')
        self.sections = [struct.unpack_from('<10I', raw, start + i * size)
                         for i in range(count)]
        self.symbols = {}
        for section in self.sections:
            if section[1] != 2:
                continue
            if section[9] != 16 or section[6] >= count:
                raise ElfAuditError('Invalid symbol table')
            strings = self.section_bytes(self.sections[section[6]])
            data = self.section_bytes(section)
            for offset in range(0, len(data) - 15, 16):
                name, value, length, info, other, shndx = struct.unpack_from('<IIIBBH', data, offset)
                end = strings.find(b'\0', name)
                if name >= len(strings) or end < 0:
                    raise ElfAuditError('Invalid symbol name')
                text = strings[name:end].decode('ascii', 'strict')
                if text and 0 < shndx < count:
                    self.symbols[text] = (value, length, shndx)

    def section_bytes(self, section):
        start, size = section[4], section[5]
        if start + size > len(self.raw):
            raise ElfAuditError('ELF section is truncated')
        return self.raw[start:start + size]

    def address(self, symbol):
        if symbol not in self.symbols:
            raise ElfAuditError('Missing symbol: ' + symbol)
        return self.symbols[symbol][0]

    def file_offset(self, symbol):
        self.address(symbol)
        address, length, number = self.symbols[symbol]
        section = self.sections[number]
        if section[1] == 8 or address < section[3] or address + length > section[3] + section[5]:
            raise ElfAuditError('Symbol is not initialized data: ' + symbol)
        return section[4] + address - section[3]

    def data(self, symbol, size):
        if self.symbols.get(symbol, (None, None))[1] != size:
            raise ElfAuditError('Wrong compiled object size: ' + symbol)
        start = self.file_offset(symbol)
        return self.raw[start:start + size]


def require(condition, message):
    if not condition:
        raise ElfAuditError(message)


def audit(raw: bytes) -> dict:
    elf = MipsElf(raw)
    for speed, packet in (('hi', 512), ('full', 64)):
        interfaces = 'pdc_interfaces_' + speed
        endpoints = 'pdc_endpoints_' + speed
        settings = 'pdc_settings_' + speed
        device = elf.data('pdc_device_' + speed, 20)
        require(device[:2] == b'\x12\x01' and struct.unpack_from('<H', device, 2)[0] == 0x0200,
                'USB 2.0 must be declared even at full speed for Windows OS descriptors')
        require(device[4:8] == b'\0\0\0\x40' and device[17] == 1,
                'Composite parent class, EP0 size or configuration count invalid')
        require(struct.unpack_from('<H', device, 12)[0] == 0x0203, 'Wrong reviewed firmware revision')
        table = struct.unpack('<6I', elf.data(settings, 24))
        expected = (elf.address(interfaces), 0, 1, elf.address(interfaces) + 24, 0, 1)
        require(table == expected, 'Need two per-interface settings records, not two descriptor pointers')
        config = elf.data('pdc_configdesc_' + speed, 24)
        require(config[:5] == b'\x09\x02\x39\x00\x02', 'Composite wire length/interface count invalid')
        require(struct.unpack_from('<I', config, 12)[0] == elf.address(settings), 'Configuration points to wrong settings')
        internal = struct.unpack('<4I', elf.data('pdc_config_' + speed, 16))
        require(internal == (elf.address('pdc_configdesc_' + speed), elf.address(settings),
                             elf.address(interfaces), elf.address(endpoints)), 'Internal configuration pointers invalid')
        desc = elf.data(interfaces, 72)
        require(desc[:9] == bytes((9,4,0,0,1,3,0,0,1)), 'HID interface metadata changed')
        require(desc[24:33] == bytes((9,4,1,0,2,255,80,2,1)), 'Vendor file interface metadata changed')
        require(struct.unpack_from('<I', desc, 12)[0] == elf.address(endpoints), 'HID endpoint pointer invalid')
        require(struct.unpack_from('<I', desc, 36)[0] == elf.address(endpoints) + 16, 'Bulk endpoint pointer invalid')
        require(struct.unpack_from('<II', desc, 16) == (elf.address('pdc_hid_descriptor'), 9), 'HID extra descriptor missing')
        require(desc[48:] == bytes(24), 'Interface sentinel corrupted')
        eps = elf.data(endpoints, 64)
        for i, (address, kind, mps) in enumerate(((129,3,8),(130,2,packet),(3,2,packet))):
            require(eps[i*16:i*16+6] == struct.pack('<BBBBH', 7,5,address,kind,mps),
                    'Endpoint direction/type/maximum packet mismatch')
        require(eps[48:] == bytes(16), 'Endpoint sentinel corrupted')
        for symbol in (interfaces, endpoints, 'pdc_device_' + speed):
            require(elf.address(symbol) % 64 == 0, 'DMA descriptor array is not cache-line aligned')
    require(elf.data('pdc_interface', 12) == struct.pack('<iii', -1,0,2), 'Driver does not own exactly two interfaces')
    require(struct.unpack('<12I', elf.data('pdc_endpoints', 48)) ==
            (0,0,0,1,0,0,2,0,0,3,0,0), 'Driver endpoint numbering invalid')
    forbidden = ('sceIoWrite','sceIoRemove','sceIoRename','sceIoMkdir','sceIoRmdir',
                 'sceIoDevctl','sceIoFormat')
    require(not any(name in elf.symbols for name in forbidden), 'Unexpected filesystem write import')
    return {'elf_machine': 'MIPS', 'revision': '0203', 'interfaces': 2,
            'settings_records_per_speed': 2, 'descriptor_tables_verified': True,
            'physical_enumeration_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('elf', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.elf.read_bytes()), sort_keys=True))
