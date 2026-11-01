import struct
from pathlib import Path
import tempfile
import unittest
import zlib
from random import Random

import psp_artwork as art


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data +
            struct.pack(">I", zlib.crc32(kind + data)))


def fixture_png(rgb=(32, 160, 210)) -> bytes:
    scanline = b"\0" + bytes(rgb) * 144
    return (
        art.PNG_MAGIC +
        png_chunk(b"IHDR", struct.pack(">IIBBBBB", 144, 80, 8, 2, 0, 0, 0)) +
        png_chunk(b"IDAT", zlib.compress(scanline * 80)) +
        png_chunk(b"IEND", b"")
    )


PNG = fixture_png()


def rec(name: bytes, extent: int, size: int, directory: bool) -> bytes:
    length = 33 + len(name) + (0 if len(name) % 2 else 1)
    result = bytearray(length)
    result[0] = length
    result[1] = 0
    struct.pack_into("<I", result, 2, extent)
    struct.pack_into(">I", result, 6, extent)
    struct.pack_into("<I", result, 10, size)
    struct.pack_into(">I", result, 14, size)
    result[25] = 2 if directory else 0
    struct.pack_into("<H", result, 28, 1)
    struct.pack_into(">H", result, 30, 1)
    result[32] = len(name)
    result[33:33 + len(name)] = name
    return bytes(result)


def write_fixture_iso(path: Path, icon: bytes = PNG) -> None:
    disc = bytearray(32 * art.SECTOR)
    pvd = 16 * art.SECTOR
    disc[pvd:pvd + 7] = b"\x01CD001\x01"
    root = rec(b"\0", 20, art.SECTOR, True)
    disc[pvd + 156:pvd + 156 + len(root)] = root
    game = rec(b"PSP_GAME", 21, art.SECTOR, True)
    disc[20 * art.SECTOR:20 * art.SECTOR + len(game)] = game
    icon_entry = rec(b"ICON0.PNG;1", 22, len(icon), False)
    disc[21 * art.SECTOR:21 * art.SECTOR + len(icon_entry)] = icon_entry
    if len(icon) > art.SECTOR:
        raise ValueError("Fixture icon too large.")
    disc[22 * art.SECTOR:22 * art.SECTOR + len(icon)] = icon
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(disc)


def write_fixture_cso(path: Path, iso: bytes) -> None:
    """Small deterministic classic CISO v1 with mixed raw/deflate sectors."""
    assert len(iso) % art.SECTOR == 0
    count = len(iso) // art.SECTOR
    next_address = 24 + 4 * (count + 1)
    index = []
    blocks = []
    for n in range(count):
        data = iso[n * art.SECTOR:(n + 1) * art.SECTOR]
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        zipped = compressor.compress(data) + compressor.flush()
        stored = len(zipped) >= len(data)
        index.append(next_address | (0x80000000 if stored else 0))
        encoded = data if stored else zipped
        blocks.append(encoded)
        next_address += len(encoded)
    index.append(next_address)
    header = b'CISO' + struct.pack('<IQIBBH', 24, len(iso), art.SECTOR, 1, 0, 0)
    path.write_bytes(header + struct.pack('<' + str(len(index)) + 'I', *index)
                     + b''.join(blocks))


class PSPArtworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_umd_iso_icon_bounded(self):
        iso = self.base / "game.iso"
        write_fixture_iso(iso)
        self.assertEqual(art.read_icon(iso), PNG)
        disc = bytearray(iso.read_bytes())
        pvd = 16 * art.SECTOR
        disc[22 * art.SECTOR] = 0
        iso.write_bytes(disc)
        self.assertIsNone(art.read_icon(iso))
        disc[22 * art.SECTOR] = PNG[0]
        disc[pvd:pvd + 7] = b"BROKEN!"
        iso.write_bytes(disc)
        self.assertIsNone(art.read_icon(iso))

    def test_read_pbp_icon_exact_offset_bounds(self):
        pbp = self.base / "EBOOT.PBP"
        start = 48
        offsets = [40, start, start + len(PNG)] + [start + len(PNG)] * 5
        pbp.write_bytes(b"\0PBP" + b"\x00\x00\x01\x00" + struct.pack("<8I", *offsets)
                        + b"\0" * 8 + PNG)
        self.assertEqual(art.read_icon(pbp), PNG)
        offsets[2] = start + art.MAX_PNG_BYTES + 1
        pbp.write_bytes(b"\0PBP" + b"\x00\x00\x01\x00" + struct.pack("<8I", *offsets)
                        + b"\0" * 8 + PNG)
        self.assertIsNone(art.read_icon(pbp))

    def test_png_safety_no_unbounded_dimensions(self):
        self.assertTrue(art.valid_png(PNG))
        self.assertFalse(art.valid_png(PNG[:8]))
        forged = bytearray(PNG)
        forged[-16] ^= 8
        self.assertFalse(art.valid_png(bytes(forged)))
        self.assertEqual(art.normalized_png(PNG + b"\0\0"), PNG)
        self.assertFalse(art.valid_png(PNG + b"<script>"))
        huge = bytearray(PNG)
        struct.pack_into(">II", huge, 16, 99999, 99999)
        self.assertFalse(art.valid_png(huge))
        self.assertFalse(art.valid_png(b"nonsense"))

    def test_unsupported_files_and_missing_psp_game(self):
        path = self.base / "game.cso"
        path.write_bytes(b"CISO")
        self.assertIsNone(art.read_icon(path))
        image = self.base / "test.png"
        image.write_bytes(PNG)
        self.assertIsNone(art.read_icon(image))

    def test_cso_v1_extracts_original_icon_without_full_decompression(self):
        iso = self.base / 'fixture.iso'
        cso = self.base / 'fixture.cso'
        write_fixture_iso(iso)
        source = bytearray(iso.read_bytes())
        source[24 * art.SECTOR:25 * art.SECTOR] = Random(31).randbytes(art.SECTOR)
        write_fixture_cso(cso, source)
        self.assertEqual(art.read_icon(cso), PNG)
        # Includes both raw and deflate blocks. No third-party decompressor.
        with cso.open('rb') as stream:
            stream.seek(24)
            flags = struct.unpack('<32I', stream.read(32 * 4))
        self.assertTrue(any(value & 0x80000000 for value in flags))
        self.assertTrue(any(not value & 0x80000000 for value in flags))
        self.assertLess(cso.stat().st_size, len(source))

        damaged = bytearray(cso.read_bytes())
        damaged[20] = 2  # Unsupported CSO v2; do not interpret as raw deflate.
        cso.write_bytes(damaged)
        self.assertIsNone(art.read_icon(cso))
        damaged[20] = 1
        struct.pack_into('<I', damaged, 24, 0)  # Index before data start.
        cso.write_bytes(damaged)
        self.assertIsNone(art.read_icon(cso))


if __name__ == "__main__":
    unittest.main()
