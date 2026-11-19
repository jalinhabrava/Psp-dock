"""Known XXH64 reference vectors and PPSSPP content-hash memory rules."""
import unittest

from psp_dock.emulation.ppsspp_texture_hash import (
    TextureHashError, PPSSPP_SEED, ppsspp_content_hash,
    display_content_hash, xxh64,
)


class XXH64PPSSPPTests(unittest.TestCase):
    def test_known_xxh64_reference_vectors(self):
        self.assertEqual(xxh64(b"",0), 0xEF46DB3751D8E999)
        self.assertEqual(xxh64(b"abc",0), 0x44BC2CF5AD770999)
        self.assertEqual(xxh64(b"hello",0), 0x26C7827D889F6DA3)
        self.assertEqual(xxh64(b"abc",PPSSPP_SEED), 0x2CCA8813ED4B54C6)
        self.assertNotEqual(xxh64(b"hello"),xxh64(b"hello",0))
        self.assertEqual(len(f"{xxh64(bytes(range(128))):016x}"), 16)

    def test_contiguous_replacement_content_hash_uses_raw_ram_not_png(self):
        raw=bytes(range(32))
        # Four 4-pixel-wide rows at 16bpp = 32 bytes.
        result=ppsspp_content_hash(raw,buffer_width=4,visible_width=4,
                                   height=4,bits_per_pixel=16)
        self.assertEqual(result,xxh64(raw,PPSSPP_SEED)&0xffffffff)
        self.assertEqual(display_content_hash(
            raw,buffer_width=4,visible_width=4,height=4,bits_per_pixel=16),
            f"{result:08x}")

    def test_padded_rows_follow_ppsspp_accumulation(self):
        raw=b"abcDEFghijKLmnop"  # two rows, 8 bytes each
        result=ppsspp_content_hash(raw,buffer_width=8,visible_width=3,
                                   height=2,bits_per_pixel=8)
        expected = ((xxh64(b"abc") & 0xffffffff)*11 ^
                    (xxh64(b"ijK") & 0xffffffff)) & 0xffffffff
        self.assertEqual(result,expected)

    def test_zero_and_invalid_metadata_never_gives_false_content_hash(self):
        raw=b"\x00" * 16
        cases=(
            {"buffer_width":0,"visible_width":4,"height":2,"bits_per_pixel":16},
            {"buffer_width":4,"visible_width":4,"height":2,"bits_per_pixel":12},
            {"buffer_width":4,"visible_width":4,"height":2,"bits_per_pixel":16,
             "reduce_hash_size":.5},
            {"buffer_width":4097,"visible_width":4,"height":2,"bits_per_pixel":16},
        )
        for args in cases:
            with self.subTest(args=args):
                with self.assertRaises(TextureHashError):
                    ppsspp_content_hash(raw,**args)
        with self.assertRaises(TextureHashError):
            ppsspp_content_hash(raw,buffer_width=8,visible_width=8,
                                height=8,bits_per_pixel=32)
        with self.assertRaises(TextureHashError):
            xxh64("raw bytes only")


if __name__=="__main__":
    unittest.main()
