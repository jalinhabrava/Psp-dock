"""Extract PSP XMB icon artwork read-only from an EBOOT.PBP or UMD ISO.

No third-party packages, no firmware emulation and no game data writes.
Bounded reads: PSP_GAME/ICON0.PNG from ISO9660 (plain ISO or CSO v1)
or the ICON0 section of a PBP. Only the sectors needed for artwork are
decompressed; no whole-disc extraction. Unsupported/corrupt media safely
returns no image. Game files are never executed.
"""
from __future__ import annotations

from pathlib import Path
import struct
import zlib


MAX_PNG_BYTES = 2 * 1024 * 1024
SECTOR = 2048
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_DIR_BYTES = 8 * 1024 * 1024
MAX_CSO_INDEX_BYTES = 16 * 1024 * 1024


def normalized_png(data: bytes) -> bytes | None:
    """Validate PNG chunk lengths/CRCs and return the exact PNG payload.

    Merely recognizing the 8-byte signature and IHDR permits truncated or
    forged artwork to be cached permanently. PBP sections may contain a few
    zero alignment bytes after IEND; they are never sent to the browser.
    """
    if not isinstance(data, bytes) or not 45 <= len(data) <= MAX_PNG_BYTES or data[:8] != PNG_MAGIC:
        return None
    pos, first, idat_found = 8, True, False
    while pos + 12 <= len(data):
        size = struct.unpack_from(">I", data, pos)[0]
        end = pos + 12 + size
        if size > MAX_PNG_BYTES or end > len(data):
            return None
        kind = data[pos + 4:pos + 8]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(data[pos + 4:end - 4]) != crc:
            return None
        if first:
            if kind != b"IHDR" or size != 13:
                return None
            width, height = struct.unpack_from(">II", data, pos + 8)
            if not (0 < width <= 2048 and 0 < height <= 2048):
                return None
            first = False
        elif kind == b"IHDR":
            return None
        if kind == b"IDAT":
            idat_found = True
        if kind == b"IEND":
            return data[:end] if (idat_found and size == 0 and len(data) - end <= 16
                                  and not any(data[end:])) else None
        pos = end
    return None


def valid_png(data: bytes) -> bool:
    return normalized_png(data) is not None


def pbp_icon(path: Path) -> bytes | None:
    with Path(path).open("rb") as f:
        header = f.read(40)
        if len(header) != 40 or header[:4] != b"\0PBP":
            return None
        offsets = struct.unpack_from("<8I", header, 8)
        total = f.seek(0, 2)
        if (sorted(offsets) != list(offsets) or offsets[0] < 40
                or offsets[-1] > total):
            return None
        start, end = offsets[1], offsets[2]
        if end <= start or end - start > MAX_PNG_BYTES:
            return None
        f.seek(start)
        image = f.read(end - start)
        return normalized_png(image)


def _directory_record(data: bytes) -> dict | None:
    if len(data) < 34 or len(data) < data[0] or data[0] < 34:
        return None
    name_size = data[32]
    if 33 + name_size > data[0]:
        return None
    extent = struct.unpack_from("<I", data, 2)[0]
    length = struct.unpack_from("<I", data, 10)[0]
    flags = data[25]
    name = data[33:33 + name_size].decode("ascii", "replace").split(";")[0].upper()
    return {"extent": extent, "size": length, "directory": bool(flags & 2),
            "name": name}


def _read_directory(stream, record: dict, file_size: int) -> list[dict]:
    if (record["size"] <= 0 or record["size"] > MAX_DIR_BYTES
            or record["extent"] <= 0):
        return []
    offset = record["extent"] * SECTOR
    end = offset + record["size"]
    if end > file_size:
        return []
    stream.seek(offset)
    entries: list[dict] = []
    data = stream.read(record["size"])
    cursor = 0
    while cursor < len(data):
        size = data[cursor]
        if not size:
            cursor = ((cursor // SECTOR) + 1) * SECTOR
            continue
        if cursor + size > len(data):
            return []
        entry = _directory_record(data[cursor:cursor + size])
        if entry is not None:
            entries.append(entry)
        cursor += size
    return entries


class _InvalidCSO(ValueError):
    pass


class _CSOReader:
    """Minimal seekable, read-only, block-indexed CSO v1 reader.

    Reject unexpected codecs/versions, absurd allocation sizes and corrupt
    index offsets. No file write APIs, device I/O, or arbitrary decompression.
    """

    def __init__(self, path: Path):
        self.file = Path(path).open('rb')
        self.position = 0
        self.cached_block = -1
        self.cached_data = b''
        try:
            file_size = self.file.seek(0, 2)
            self.file.seek(0)
            header = self.file.read(24)
            if len(header) != 24 or header[:4] != b'CISO':
                raise _InvalidCSO('Firma CSO desconocida.')
            _, self.size, self.block_size, version, shift, _ = struct.unpack_from(
                '<IQIBBH', header, 4
            )
            if (version not in (0, 1) or not SECTOR <= self.block_size <= 65536
                    or self.block_size & (self.block_size - 1)
                    or self.size < SECTOR * 17 or self.size > 8 * 1024**3
                    or shift > 20):
                raise _InvalidCSO('Parámetros CSO fuera de los límites admitidos.')
            self.shift = shift
            blocks = (self.size + self.block_size - 1) // self.block_size
            index_bytes = (blocks + 1) * 4
            if index_bytes > MAX_CSO_INDEX_BYTES or 24 + index_bytes > file_size:
                raise _InvalidCSO('Índice CSO ausente o demasiado grande.')
            index_data = self.file.read(index_bytes)
            if len(index_data) != index_bytes:
                raise _InvalidCSO('Índice CSO truncado.')
            self.index = struct.unpack('<' + str(blocks + 1) + 'I', index_data)
            addresses = [(x & 0x7fffffff) << shift for x in self.index]
            if (addresses[0] < 24 + index_bytes
                    or addresses[-1] > file_size
                    or any(a > b for a, b in zip(addresses, addresses[1:]))):
                raise _InvalidCSO('Índice CSO fuera de archivo o desordenado.')
            self.addresses = addresses
        except Exception:
            self.file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.file.close()

    def seek(self, offset: int, whence: int = 0) -> int:
        base = (0 if whence == 0 else self.position if whence == 1
                else self.size if whence == 2 else None)
        if base is None or not 0 <= base + offset <= self.size:
            raise _InvalidCSO('Lectura CSO fuera del disco virtual.')
        self.position = base + offset
        return self.position

    def _block(self, number: int) -> bytes:
        if self.cached_block == number:
            return self.cached_data
        start, end = self.addresses[number:number + 2]
        expected = min(self.block_size, self.size - number * self.block_size)
        if (end < start or end - start > self.block_size + 64 + (1 << self.shift)
                or start < 24 or end > self.addresses[-1]):
            raise _InvalidCSO('Bloque CSO con límites inválidos.')
        self.file.seek(start)
        encoded = self.file.read(end - start)
        if len(encoded) != end - start:
            raise _InvalidCSO('Bloque CSO truncado.')
        if self.index[number] & 0x80000000:
            data = encoded[:expected]
        else:
            try:
                decoder = zlib.decompressobj(-15)
                data = decoder.decompress(encoded, expected + 1)
                if not decoder.eof or decoder.unconsumed_tail:
                    raise _InvalidCSO('Deflate CSO incompleto.')
            except zlib.error as exc:
                raise _InvalidCSO('Bloque CSO corrupto.') from exc
        if len(data) != expected:
            raise _InvalidCSO('Tamaño del bloque CSO incorrecto.')
        self.cached_block, self.cached_data = number, data
        return data

    def read(self, length: int) -> bytes:
        if not 0 <= length <= MAX_DIR_BYTES:
            raise _InvalidCSO('Lectura CSO sin límite permitido.')
        length = min(length, self.size - self.position)
        output = bytearray()
        while length:
            sector, intra = divmod(self.position, self.block_size)
            data = self._block(sector)
            piece = data[intra:intra + length]
            if not piece:
                raise _InvalidCSO('Lectura CSO sin progreso.')
            output += piece
            self.position += len(piece)
            length -= len(piece)
        return bytes(output)


def _iso_stream_icon(stream) -> bytes | None:
    size = stream.seek(0, 2)
    if size < SECTOR * 17:
        return None
    stream.seek(SECTOR * 16)
    pvd = stream.read(SECTOR)
    if (len(pvd) != SECTOR or pvd[:7] != b"\x01CD001\x01"):
        return None
    root = _directory_record(pvd[156:])
    if not root or not root["directory"]:
        return None
    first = _read_directory(stream, root, size)
    game = next((r for r in first if r["name"] == "PSP_GAME"
                 and r["directory"]), None)
    if game is None:
        return None
    second = _read_directory(stream, game, size)
    icon = next((r for r in second if r["name"] == "ICON0.PNG"
                 and not r["directory"]), None)
    if (icon is None or icon["size"] < 45 or icon["size"] > MAX_PNG_BYTES
            or icon["extent"] * SECTOR + icon["size"] > size):
        return None
    stream.seek(icon["extent"] * SECTOR)
    image = stream.read(icon["size"])
    return normalized_png(image)


def iso_icon(path: Path) -> bytes | None:
    with Path(path).open("rb") as stream:
        return _iso_stream_icon(stream)


def cso_icon(path: Path) -> bytes | None:
    try:
        with _CSOReader(path) as stream:
            return _iso_stream_icon(stream)
    except _InvalidCSO:
        return None


def read_icon(path: Path) -> bytes | None:
    """Use the file extension only for parsing. Always bounded read-only."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pbp":
        return pbp_icon(path)
    if suffix == ".iso":
        return iso_icon(path)
    if suffix == ".cso":
        return cso_icon(path)
    return None
