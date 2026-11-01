"""Validate the EBOOT's XMB art and PBP section offsets without a PSP.

python3 tests/test_pbp_assets.py dist/EBOOT.PBP
"""
from __future__ import annotations

from pathlib import Path
import struct
import sys
import unittest


APP = Path(__file__).resolve().parents[1]
PBP = APP / "dist/EBOOT.PBP"


def png_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        raise AssertionError("Invalid PNG header")
    return struct.unpack_from(">II", data, 16)


class PbpArtworkTest(unittest.TestCase):
    def test_assets_exist_at_psp_dimensions(self) -> None:
        for name, expected in (
            ("ICON0.PNG", (144, 80)), ("PIC0.PNG", (310, 180)),
            ("PIC1.PNG", (480, 272)),
        ):
            data = (APP / "assets" / name).read_bytes()
            self.assertEqual(png_dimensions(data), expected)
            self.assertLess(len(data), 300_000)

    def test_pbp_preserves_embedded_artwork(self) -> None:
        if not PBP.is_file():
            self.skipTest("Build EBOOT.PBP via build-wsl.sh to check PBP sections")
        pbp = PBP.read_bytes()
        self.assertEqual(pbp[:4], b"\x00PBP")
        offsets = struct.unpack_from("<8I", pbp, 8)
        self.assertGreaterEqual(offsets[0], 40)
        self.assertEqual(sorted(offsets), list(offsets))
        # DATA.PSAR may be NULL, in which case its start equals EOF.
        self.assertLessEqual(offsets[-1], len(pbp))
        for index, name, expected in (
            (1, "ICON0.PNG", (144, 80)),
            (3, "PIC0.PNG", (310, 180)),
            (4, "PIC1.PNG", (480, 272)),
        ):
            embedded = pbp[offsets[index]:offsets[index + 1]]
            self.assertEqual(embedded, (APP / "assets" / name).read_bytes())
            self.assertEqual(png_dimensions(embedded), expected)
        self.assertGreater(offsets[7] - offsets[6], 50_000)  # EBOOT executable


if __name__ == "__main__":
    if len(sys.argv) > 1:
        PBP = Path(sys.argv[1]).resolve()
    unittest.main(argv=[sys.argv[0]])
