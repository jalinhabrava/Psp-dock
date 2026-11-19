"""Reusable deterministic codecs, PSP GE formats and fail-closed DZIP tests."""
import gzip
import hashlib
from io import BytesIO
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from psp_dock.emulation.psp_texture_engine import (
    CodecFixture, RawTextureSpec, TextureEngineError, UnsupportedCodec,
    analyze_resource, decode_raw_texture, decompress, identify, inspect_file,
    unswizzle, validate_codec,
)
from psp_dock.emulation.ppsspp_texture_hash import xxh64
from psp_dock.emulation.psp_hd_textures import discover_iso_assets
from tests.test_psp_dariusburst_re import synthetic_dariusburst_iso

try:
    from PIL import Image
except ImportError:
    Image = None


class FormatDetectorTests(unittest.TestCase):
    def test_same_dzip_header_detected_independent_of_game_or_extension(self):
        first = b"DZIP" + struct.pack("<I", 1024) + bytes.fromhex("840760f2") + b"opaque"
        second = b"DZIP" + struct.pack("<I", 2048) + bytes.fromhex("85074476") + b"opaque"
        a, b = identify(first, extension=".tx2"), identify(second, extension=".xxy")
        self.assertEqual(a.family, "dzip_unknown")
        self.assertEqual(b.family, "dzip_unknown")
        self.assertEqual(a.signature, b.signature)
        self.assertEqual(a.codec, None)
        self.assertNotEqual(a.variant_prefix, b.variant_prefix)
        self.assertEqual(a.declared_size, 1024)
        self.assertEqual(b.declared_size, 2048)
        self.assertEqual(a.status, "recognized_unsupported")
        self.assertFalse(a.report()["fully_decoded"])
        self.assertFalse(a.report()["ppsspp_hash_verified"])
        self.assertFalse(analyze_resource(first).report()["replacement_installable"])

    def test_similarly_named_marmalade_dzip_not_misclassified(self):
        other = identify(b"DTRZ" + b"\x01" * 14, extension=".dz")
        self.assertEqual(other.family, "marmalade_dzip")
        self.assertNotEqual(other.signature, "445a4950")
        self.assertIsNone(other.codec)
        with self.assertRaises(UnsupportedCodec):
            decompress(b"opaque", codec="dzip", expected_size=50)

    def test_reject_invalid_dzip_header_and_unrecognized_input(self):
        self.assertEqual(identify(b"DZIP").status,"malformed")
        self.assertEqual(identify(b"DZIP" + b"\xff" * 8).status,"malformed")
        self.assertEqual(identify(b"somegame\x00texture").status,"unrecognized")
        with self.assertRaises(TextureEngineError):
            identify(b"0" * 20,extension="."*50)

    def test_file_probe_is_read_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "sample.any"
            file.write_bytes(b"DZIP" + struct.pack("<I", 100) + b"\x84\x07\x60\xf2")
            before = file.read_bytes()
            report = inspect_file(file)
            self.assertEqual(report["family"],"dzip_unknown")
            self.assertEqual(report["source_sha256"],hashlib.sha256(before).hexdigest())
            self.assertEqual(report["input_bytes"],len(before))
            self.assertEqual(file.read_bytes(), before)


class CodecTests(unittest.TestCase):
    def test_verified_codec_is_reusable_without_disc_id(self):
        a = b"\x10" * 40 + b"first valid payload"
        b = bytes(range(63))
        fixtures = tuple(CodecFixture(
            zlib.compress(payload), hashlib.sha256(payload).hexdigest(),
            len(payload), f"sample-{i}") for i,payload in enumerate((a,b))
        )
        self.assertEqual(validate_codec("zlib",fixtures), {
            "codec":"zlib", "tested_samples":2,
            "verified":True,"game_specific":False})
        for item,expected in zip(fixtures,(a,b)):
            self.assertEqual(decompress(item.packed,codec="zlib",
                                        expected_size=item.decoded_size),expected)

    def test_zlib_gzip_and_explicit_deflate_roundtrip(self):
        raw=b"\x01\x02"*1024
        for codec,packed in [
            ("zlib", zlib.compress(raw)),
            ("gzip", gzip.compress(raw)),
            ("raw_deflate", self._raw_deflate(raw)),
        ]:
            self.assertEqual(decompress(packed,codec=codec,
                                        expected_size=len(raw)),raw)
        self.assertEqual(identify(zlib.compress(raw)).family,"zlib")
        self.assertEqual(identify(gzip.compress(raw)).family,"gzip")
        self.assertEqual(analyze_resource(zlib.compress(raw)).status,"needs_exact_size")

    @staticmethod
    def _raw_deflate(raw):
        compressor = zlib.compressobj(wbits=-15)
        return compressor.compress(raw) + compressor.flush()

    def test_bad_size_trailing_garbage_and_zipbomb_fail(self):
        payload=zlib.compress(b"a"*200_000)
        for packed,claimed in ((payload,100),(payload+b"extra",200_000)):
            with self.assertRaises(TextureEngineError):
                decompress(packed,codec="zlib",expected_size=claimed)
        with self.assertRaises(TextureEngineError):
            decompress(b"",codec="gzip",expected_size=5)
        with self.assertRaises(TextureEngineError):
            decompress(payload,codec="zlib",expected_size=64*1024*1024+1)

    def test_codec_proof_requires_different_independent_sha(self):
        raw=b"hello"
        item=CodecFixture(zlib.compress(raw),hashlib.sha256(raw).hexdigest(),
                          len(raw),"same")
        with self.assertRaises(TextureEngineError):
            validate_codec("zlib",(item,item))
        with self.assertRaises(TextureEngineError):
            validate_codec("zlib",(item,))
        invalid=CodecFixture(item.packed,"0"*64,item.decoded_size,"second")
        with self.assertRaises(TextureEngineError):
            validate_codec("zlib",(item,invalid))


class TextureFormatTests(unittest.TestCase):
    def test_rgba8888_exact_rgba_and_partial_hash(self):
        pixels = bytes([255,0,0,255, 0,255,0,128, 0,0,255,255, 20,30,40,50])
        texture = decode_raw_texture(pixels,RawTextureSpec(2,2,2,"rgba8888"))
        self.assertEqual(texture.rgba,pixels)
        self.assertEqual(texture.pixels_sha256,hashlib.sha256(pixels).hexdigest())
        self.assertEqual(texture.ppsspp_content_hash_8,
                         f"{xxh64(pixels)&0xffffffff:08x}")
        self.assertFalse(texture.full_replacement_key_verified)
        self.assertFalse(texture.report()["full_replacement_key_verified"])

    def test_psp_raw_rgb565_and_rgba4444_5551(self):
        red = (0x001f).to_bytes(2,"little")
        blue = (0xf800).to_bytes(2,"little")
        self.assertEqual(
            decode_raw_texture(red+blue,RawTextureSpec(2,1,2,"rgb565")).rgba,
            bytes((255,0,0,255, 0,0,255,255)))
        self.assertEqual(
            decode_raw_texture((0xf00f).to_bytes(2,"little"),
                               RawTextureSpec(1,1,1,"rgba4444")).rgba,
            bytes((255,0,0,255)))
        self.assertEqual(
            decode_raw_texture((0x03e0).to_bytes(2,"little"),
                               RawTextureSpec(1,1,1,"rgba5551")).rgba,
            bytes((0,255,0,0)))

    def test_indexed_4_and_8_with_explicit_palette(self):
        palette4 = b"".join(bytes((i,i*2,i*3,255)) for i in range(16))
        indexed4 = decode_raw_texture(
            bytes((0x21,0x43)), RawTextureSpec(4,1,4,"clut4",
                                                palette_format="rgba8888",
                                                palette=palette4))
        self.assertEqual(list(indexed4.rgba[:4]),[1,2,3,255])
        self.assertEqual(list(indexed4.rgba[4:8]),[2,4,6,255])
        self.assertEqual(list(indexed4.rgba[8:12]),[3,6,9,255])
        palette8 = (0).to_bytes(2,"little") * 255 + (0x001f).to_bytes(2,"little")
        indexed8 = decode_raw_texture(
            bytes((255,)),RawTextureSpec(1,1,1,"clut8",
                                          palette_format="rgb565",palette=palette8))
        self.assertEqual(indexed8.rgba,bytes((255,0,0,255)))

    def test_swizzle_16x8_tiles_returns_original_linear_pixels(self):
        linear = bytes((x%256 for x in range(32*8)))
        swizzled = b"".join(
            linear[y*32+bx*16:y*32+(bx+1)*16]
            for bx in range(2) for y in range(8))
        self.assertNotEqual(swizzled,linear)
        self.assertEqual(unswizzle(swizzled,stride=32,height=8),linear)
        with self.assertRaises(TextureEngineError):
            unswizzle(swizzled,stride=30,height=8)

    def test_reject_nonaligned_clut_and_truncated_gpu_data(self):
        with self.assertRaises(TextureEngineError):
            RawTextureSpec(3,1,3,"clut4",palette=b"\x00"*64,
                           palette_format="rgba8888")
        with self.assertRaises(TextureEngineError):
            RawTextureSpec(2,1,2,"clut8",palette=b"\x00"*63,
                           palette_format="rgba8888")
        with self.assertRaises(TextureEngineError):
            decode_raw_texture(b"\x00"*3,RawTextureSpec(2,2,2,"rgba8888"))


@unittest.skipIf(Image is None,"Pillow opcional no instalado")
class PipelineTests(unittest.TestCase):
    def test_end_to_end_generic_png_in_zlib_requires_no_game_adapter(self):
        image=Image.new("RGBA",(8,4),(140,70,30,255))
        output=BytesIO()
        image.save(output,format="PNG")
        png=output.getvalue()
        compressed=zlib.compress(png)
        for _game in ("GAME-A", "GAME-B"):
            result=analyze_resource(compressed,expected_size=len(png))
            self.assertEqual(result.status,"texture_decoded")
            self.assertEqual(result.texture.width,8)
            self.assertEqual(result.texture.height,4)
            self.assertEqual(result.detected.codec,"zlib")
            self.assertFalse(result.report()["replacement_installable"])
        self.assertEqual(analyze_resource(png).status,"texture_decoded")

    def test_end_to_end_generic_packed_raw_gpu_with_declared_metadata(self):
        raw=bytes((200,150,100,255))*4
        packed=gzip.compress(raw)
        result=analyze_resource(packed,expected_size=len(raw),
                                raw_texture=RawTextureSpec(2,2,2,"rgba8888"))
        self.assertEqual(result.status,"texture_decoded")
        self.assertEqual(result.texture.rgba,raw)
        self.assertEqual(result.texture.ppsspp_content_hash_8,
                         f"{xxh64(raw)&0xffffffff:08x}")
        self.assertFalse(result.texture.full_replacement_key_verified)
        self.assertFalse(result.report()["replacement_installable"])


class ISOScannerTests(unittest.TestCase):
    def test_global_iso_discovery_sees_dzip_without_game_specific_name(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"anything.iso"
            content=synthetic_dariusburst_iso(path)
            report=discover_iso_assets(path,Path(root)/"cache",disc_id="ULJM05558")
            self.assertEqual(report["files_visited"],5)
            self.assertEqual(report["recognized_unsupported"],3)
            self.assertEqual(report["format_profiles"],{"dzip_unknown":3})
            self.assertEqual(len([x for x in report["candidates"]
                                  if x["format"]=="dzip_unknown"]),3)
            self.assertTrue(all(x["mapped"] is False for x in report["candidates"]))
            self.assertEqual(path.read_bytes(),content)


if __name__=="__main__":
    unittest.main()
