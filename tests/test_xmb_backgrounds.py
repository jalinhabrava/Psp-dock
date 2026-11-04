import concurrent.futures
import hashlib
import io
import os
from pathlib import Path
import stat
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, ImageChops, PngImagePlugin

import psp_dock.media.xmb_backgrounds as backgrounds


def png(size=(16, 8), color=(35, 80, 125), mode="RGB", **options):
    with Image.new(mode, size, color) as image, io.BytesIO() as stream:
        image.save(stream, format="PNG", **options)
        return stream.getvalue()


def png_bytes(image, **options):
    with io.BytesIO() as stream:
        image.save(stream, format="PNG", **options)
        return stream.getvalue()


def changed_header(source, width, height):
    data = bytearray(source)
    struct.pack_into(">II", data, 16, width, height)
    struct.pack_into(">I", data, 29, zlib.crc32(data[12:29]) & 0xffffffff)
    return bytes(data)


def broken_idat(source):
    data = bytearray(source)
    offset = 8
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        if data[offset + 4:offset + 8] == b"IDAT":
            data[offset + 8:offset + 10] = b"\0\0"
            crc = zlib.crc32(data[offset + 4:offset + 8 + length]) & 0xffffffff
            struct.pack_into(">I", data, offset + 8 + length, crc)
            return bytes(data)
        offset += length + 12
    raise AssertionError("Fixture lacks IDAT.")


class BackgroundCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Exercise real 3840-wide output in the geometry tests. Other tests use
        # small output to spend their time on actual queue/filesystem behaviour.
        target = patch.object(backgrounds, "TARGET_WIDTH", 64)
        target.start()
        self.addCleanup(target.stop)

    def cache(self, folder="cache"):
        cache = backgrounds.BackgroundCache(self.root / folder)
        self.addCleanup(cache.close, wait=True)
        return cache

    def ready(self, cache, source):
        self.assertIsNone(cache.request(source))
        self.assertTrue(cache.wait(timeout=30), "Worker did not finish.")
        result = cache.request(source)
        self.assertIsInstance(result, bytes)
        self.assertLessEqual(len(result), backgrounds.MAX_OUTPUT_BYTES)
        with Image.open(io.BytesIO(result)) as image:
            image.verify()
        return result

    def link(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest("This host cannot create fixture symlinks.")

    def test_psp_background_preserves_aspect_and_original(self):
        source = png((480, 272))
        original = self.root / "original.png"
        original.write_bytes(source)
        before = original.stat().st_mtime_ns
        with patch.object(backgrounds, "TARGET_WIDTH", 3840):
            result = self.ready(self.cache(), source)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.size, (3840, 2176))
            self.assertEqual(image.mode, "RGB")
        self.assertEqual(original.read_bytes(), source)
        self.assertEqual(original.stat().st_mtime_ns, before)

    def test_square_portrait_and_extreme_aspects_stay_bounded(self):
        fixtures = [((480, 480), (3162, 3162)),
                    ((400, 800), (2048, 4096)),
                    ((4096, 1), (3840, 1)),
                    ((1, 4096), (1, 4096))]
        with patch.object(backgrounds, "TARGET_WIDTH", 3840):
            cache = self.cache()
            for dimensions, expected in fixtures:
                with self.subTest(dimensions=dimensions):
                    value = self.ready(cache, png(dimensions))
                    with Image.open(io.BytesIO(value)) as image:
                        self.assertEqual(image.size, expected)
                        self.assertLessEqual(max(image.size), 4096)
                        self.assertLessEqual(image.width * image.height, 10_000_000)

    def test_alpha_is_resampled_not_sharpened_and_hidden_rgb_does_not_bleed(self):
        source = Image.new("RGBA", (8, 4), (255, 0, 0, 0))
        for y in range(4):
            for x in range(3, 8):
                source.putpixel((x, y), (0, 200, 0, 80 if x < 5 else 255))
        value = self.ready(self.cache(), png_bytes(source))
        with Image.open(io.BytesIO(value)) as image:
            self.assertEqual(image.mode, "RGBA")
            expected_alpha = source.getchannel("A").resize(image.size, Image.Resampling.LANCZOS)
            self.assertEqual(image.getchannel("A").tobytes(), expected_alpha.tobytes())
            self.assertEqual(image.getchannel("R").getextrema(), (0, 0))
            self.assertEqual(image.getchannel("B").getextrema(), (0, 0))

    def test_palette_index_zero_and_grayscale_alpha_are_preserved(self):
        palette = Image.new("P", (8, 4), 0)
        palette.putpalette([255, 0, 0, 0, 200, 0] + [0] * 762)
        palette.paste(1, (4, 0, 8, 4))
        sources = [png_bytes(palette, transparency=0), png(mode="LA", color=(80, 119))]
        cache = self.cache()
        for source in sources:
            value = self.ready(cache, source)
            with Image.open(io.BytesIO(source)) as before, Image.open(io.BytesIO(value)) as after:
                expected = before.convert("RGBA").getchannel("A").resize(after.size, Image.Resampling.LANCZOS)
                self.assertEqual(after.mode, "RGBA")
                self.assertEqual(after.getchannel("A").tobytes(), expected.tobytes())

    def test_grayscale_16_bit_does_not_clip_to_white(self):
        source = Image.new("I;16", (16, 8), 32768)
        value = self.ready(self.cache(), png_bytes(source))
        with Image.open(io.BytesIO(value)) as image:
            self.assertEqual(image.getpixel((20, 10)), (127, 127, 127))

    def test_grayscale_16_bit_transparency_survives_downconversion(self):
        def chunk(kind, payload):
            return (struct.pack(">I", len(payload)) + kind + payload +
                    struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

        source = (b"\x89PNG\r\n\x1a\n" +
                  chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 16, 0, 0, 0, 0)) +
                  chunk(b"tRNS", struct.pack(">H", 32768)) +
                  chunk(b"IDAT", zlib.compress(b"\0" + struct.pack(">HH", 32768, 65535))) +
                  chunk(b"IEND", b""))
        value = self.ready(self.cache(), source)
        alpha = Image.new("L", (2, 1), 255)
        alpha.putpixel((0, 0), 0)
        with Image.open(io.BytesIO(value)) as image:
            self.assertEqual(image.mode, "RGBA")
            expected = alpha.resize(image.size, Image.Resampling.LANCZOS)
            self.assertEqual(image.getchannel("A").tobytes(), expected.tobytes())
            self.assertEqual(image.getpixel((image.width - 1, 0)), (255, 255, 255, 255))

    def test_sharpening_has_a_per_channel_bound(self):
        source = Image.new("RGB", (8, 4), (20, 70, 180))
        source.paste((230, 120, 10), (4, 0, 8, 4))
        value = self.ready(self.cache(), png_bytes(source))
        with Image.open(io.BytesIO(value)) as image:
            plain = source.resize(image.size, Image.Resampling.LANCZOS)
            extrema = ImageChops.difference(image, plain).getextrema()
            self.assertTrue(all(high <= 6 for _, high in extrema))
            self.assertTrue(any(high > 0 for _, high in extrema))

    def test_memory_hit_never_decodes_or_reads_files_again(self):
        source = png()
        cache = self.cache()
        value = self.ready(cache, source)
        with patch.object(cache, "_build", side_effect=AssertionError("Duplicate work")) as build:
            for _ in range(20):
                self.assertIs(cache.request(source), value)
            self.assertTrue(cache.wait(timeout=1))
        build.assert_not_called()

    def test_fresh_instance_uses_verified_disk_cache_without_rerendering(self):
        source = png()
        first = self.cache()
        value = self.ready(first, source)
        first.close(wait=True)
        path = next(first.root.glob("*.png"))
        modified = path.stat().st_mtime_ns
        with patch.object(backgrounds, "_render_background", side_effect=AssertionError("Disk hit rerendered")) as render:
            second = self.cache()
            self.assertEqual(self.ready(second, source), value)
        render.assert_not_called()
        self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_key_tracks_exact_source_bytes_and_algorithm_version(self):
        first = png()
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("note", "same pixels, different original bytes")
        second = png(pnginfo=metadata)
        cache = self.cache()
        self.ready(cache, first)
        self.ready(cache, second)
        original_names = {path.name for path in cache.root.glob("*.png")}
        self.assertEqual(len(original_names), 2)
        expected = hashlib.sha256(backgrounds.ALGORITHM_VERSION.encode("ascii") + b"\0" + first).hexdigest()
        self.assertIn(expected + ".png", original_names)
        with patch.object(backgrounds, "ALGORITHM_VERSION", "test-next-algorithm"):
            self.ready(cache, first)
        self.assertEqual(len(list(cache.root.glob("*.png"))), 3)

    def test_invalid_headers_types_and_bombs_are_rejected_before_queuing(self):
        source = png()
        invalid = [None, b"", bytearray(source), "file.png", b"GIF89a" + b"x" * 100,
                   changed_header(source, 4097, 1), changed_header(source, 1, 4097),
                   changed_header(source, 4096, 1954), changed_header(source, 0, 20),
                   source + b"x" * backgrounds.MAX_INPUT_BYTES]
        cache = self.cache()
        with patch.object(backgrounds, "_decode_png", side_effect=AssertionError("Header should be rejected")) as decode:
            for value in invalid:
                self.assertIsNone(cache.request(value))
            self.assertTrue(cache.wait(timeout=0))
        decode.assert_not_called()
        self.assertFalse(cache.root.exists())

    def test_crc_truncation_and_invalid_compression_never_become_ready(self):
        source = png()
        broken_crc = bytearray(source)
        broken_crc[-1] ^= 0xff
        compressed = broken_idat(source)
        with Image.open(io.BytesIO(compressed)) as image:
            image.verify()  # Container is intact; pixel decoding must also run.
        cache = self.cache()
        with patch.object(backgrounds, "_render_background", side_effect=AssertionError("Corrupt source reached render")) as render:
            for invalid in (bytes(broken_crc), source[:-5], compressed, source + b"trailer"):
                self.assertIsNone(cache.request(invalid))
                self.assertTrue(cache.wait(timeout=5))
                self.assertIsNone(cache.request(invalid))
        render.assert_not_called()
        self.assertFalse(cache.root.exists())

    def test_animated_png_is_rejected(self):
        first = Image.new("RGBA", (8, 4), (20, 40, 80, 255))
        second = Image.new("RGBA", (8, 4), (80, 40, 20, 255))
        source = png_bytes(first, save_all=True, append_images=[second], duration=100)
        cache = self.cache()
        self.assertIsNone(cache.request(source))
        self.assertTrue(cache.wait(timeout=5))
        self.assertIsNone(cache.request(source))
        self.assertFalse(cache.root.exists())

    def test_corrupt_oversized_or_wrong_cached_png_is_regenerated(self):
        source = png()
        original = self.cache()
        value = self.ready(original, source)
        original.close(wait=True)
        path = next(original.root.glob("*.png"))
        for corruption in (b"broken", png((64, 32)), "oversized"):
            with self.subTest(corruption=str(corruption)[:30]):
                if corruption == "oversized":
                    with path.open("wb") as stream:
                        stream.truncate(backgrounds.MAX_OUTPUT_BYTES + 1)
                else:
                    path.write_bytes(corruption)
                cache = self.cache()
                with patch.object(backgrounds, "_render_background", wraps=backgrounds._render_background) as render:
                    self.assertEqual(self.ready(cache, source), value)
                    render.assert_called_once()
                cache.close(wait=True)
                self.assertEqual(path.read_bytes(), value)

    def test_cache_must_match_expected_dimensions_and_alpha(self):
        source = png(mode="RGBA", color=(20, 80, 120, 90))
        cache = self.cache()
        expected = self.ready(cache, source)
        cache.close(wait=True)
        path = next(cache.root.glob("*.png"))
        tag = PngImagePlugin.PngInfo()
        tag.add_text("PSPDockBackgroundKey", path.stem)
        for dimensions, mode in [((60, 30), "RGBA"), ((64, 32), "RGB")]:
            color = (20, 80, 120, 90) if mode == "RGBA" else (20, 80, 120)
            path.write_bytes(png(dimensions, color, mode, pnginfo=tag))
            cache = self.cache()
            self.assertEqual(self.ready(cache, source), expected)
            cache.close(wait=True)

    def test_concurrent_duplicate_requests_render_once_on_one_worker(self):
        source = png()
        cache = self.cache()
        entered, release = threading.Event(), threading.Event()
        threads = set()
        real = backgrounds._render_background

        def blocked(image, key):
            threads.add(threading.get_ident())
            entered.set()
            if not release.wait(10):
                raise AssertionError("Test did not release the renderer.")
            return real(image, key)

        with patch.object(backgrounds, "_render_background", side_effect=blocked) as render:
            try:
                self.assertIsNone(cache.request(source))
                self.assertTrue(entered.wait(5))
                with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                    futures = [pool.submit(cache.request, source) for _ in range(48)]
                    self.assertTrue(all(future.result(timeout=2) is None for future in futures))
                self.assertFalse(cache.wait(timeout=0))
            finally:
                release.set()
            self.assertTrue(cache.wait(timeout=10))
            self.assertIsInstance(cache.request(source), bytes)
            render.assert_called_once()
        self.assertEqual(len(threads), 1)
        self.assertNotIn(threading.get_ident(), threads)

    def test_queue_holds_four_waiting_jobs_and_rejected_work_can_be_retried(self):
        cache = self.cache()
        sources = [png(color=(i * 20, 80, 120)) for i in range(6)]
        entered, release = threading.Event(), threading.Event()
        threads = set()
        real = backgrounds._render_background

        def blocked(image, key):
            threads.add(threading.get_ident())
            entered.set()
            if not release.wait(10):
                raise AssertionError("Test did not release the renderer.")
            return real(image, key)

        with patch.object(backgrounds, "_render_background", side_effect=blocked) as render:
            try:
                cache.request(sources[0])
                self.assertTrue(entered.wait(5))
                for source in sources[1:]:
                    self.assertIsNone(cache.request(source))
                self.assertEqual(len(cache._jobs), 4)
                self.assertEqual(len(cache._pending), 5)
            finally:
                release.set()
            self.assertTrue(cache.wait(timeout=10))
            self.assertEqual(render.call_count, 5)
            for source in sources[:5]:
                self.assertIsInstance(cache.request(source), bytes)
            self.ready(cache, sources[5])
            self.assertEqual(render.call_count, 6)
        self.assertEqual(len(threads), 1)

    def test_cold_disk_lookup_does_not_block_request(self):
        source = png()
        first = self.cache()
        expected = self.ready(first, source)
        first.close(wait=True)
        cache = self.cache()
        entered, release = threading.Event(), threading.Event()
        real = cache._read_cached

        def slow_read(*args):
            entered.set()
            if not release.wait(10):
                raise AssertionError("Test did not release the reader.")
            return real(*args)

        with patch.object(cache, "_read_cached", side_effect=slow_read):
            try:
                self.assertIsNone(cache.request(source))
                self.assertTrue(entered.wait(5))
                self.assertIsNone(cache.request(source))
                self.assertFalse(cache.wait(timeout=0))
            finally:
                release.set()
            self.assertTrue(cache.wait(timeout=10))
        self.assertEqual(cache.request(source), expected)

    def test_close_without_wait_returns_while_render_is_busy_and_drains_queue(self):
        cache = self.cache()
        sources = [png(color=(i * 20, 80, 120)) for i in range(5)]
        entered, release = threading.Event(), threading.Event()
        real = backgrounds._render_background

        def blocked(image, key):
            entered.set()
            if not release.wait(10):
                raise AssertionError("Test did not release the renderer.")
            return real(image, key)

        with patch.object(backgrounds, "_render_background", side_effect=blocked) as render:
            try:
                cache.request(sources[0])
                self.assertTrue(entered.wait(5))
                for source in sources[1:]:
                    cache.request(source)
                cache.close(wait=False)
                self.assertTrue(cache._worker.is_alive())
                self.assertIsNone(cache.request(png(color=(99, 99, 99))))
                self.assertFalse(cache.wait(timeout=0))
            finally:
                release.set()
            cache.close(wait=True)
            self.assertEqual(render.call_count, 5)
        self.assertFalse(cache._worker.is_alive())
        self.assertTrue(cache.wait(timeout=0))
        self.assertIsNone(cache.request(sources[0]))
        self.assertEqual(len(list(cache.root.glob("*.png"))), 5)

    def test_render_encode_and_atomic_write_failures_fall_back_and_worker_survives(self):
        source = png()
        for target in ("psp_dock.media.xmb_backgrounds._render_background", "PIL.Image.Image.save", "psp_dock.media.xmb_backgrounds.os.replace"):
            with self.subTest(target=target):
                cache = self.cache(target.replace(".", "-"))
                with patch(target, side_effect=OSError("injected failure")):
                    self.assertIsNone(cache.request(source))
                    self.assertTrue(cache.wait(timeout=5))
                    self.assertIsNone(cache.request(source))
                self.assertEqual(list(cache.root.glob("*.png")), [])
                self.assertEqual(list(cache.root.glob("*.tmp")), [])
                self.ready(cache, png(color=(40, 81, 126)))

    def test_output_byte_limit_aborts_encoding_before_publication(self):
        cache = self.cache()
        source = png()
        with patch.object(backgrounds, "MAX_OUTPUT_BYTES", 80):
            self.assertIsNone(cache.request(source))
            self.assertTrue(cache.wait(timeout=5))
            self.assertIsNone(cache.request(source))
        self.assertEqual(list(cache.root.iterdir()), [])

    def test_failed_polling_is_suppressed_and_retry_is_possible_after_cooldown(self):
        cache = self.cache()
        source = png()
        with patch("psp_dock.media.xmb_backgrounds.time.monotonic", return_value=100.0):
            with patch.object(cache, "_build", side_effect=OSError("temporary failure")) as build:
                cache.request(source)
                self.assertTrue(cache.wait(timeout=5))
                for _ in range(10):
                    self.assertIsNone(cache.request(source))
                self.assertTrue(cache.wait(timeout=0))
                build.assert_called_once()
        with patch("psp_dock.media.xmb_backgrounds.time.monotonic", return_value=131.0):
            self.ready(cache, source)

    def test_ready_memory_and_failure_history_are_bounded(self):
        cache = self.cache()
        sources = [png(color=(i * 20, 80, 120)) for i in range(4)]
        with patch.object(backgrounds, "MAX_READY_ITEMS", 2):
            for source in sources:
                self.ready(cache, source)
            self.assertEqual(len(cache._ready), 2)
        with patch.object(backgrounds, "MAX_READY_BYTES", 1):
            cache.request(png(color=(200, 80, 120)))
            self.assertTrue(cache.wait(timeout=5))
            self.assertEqual(cache._ready_bytes, 0)
            self.assertEqual(len(cache._ready), 0)
        with patch.object(backgrounds, "MAX_FAILED_KEYS", 2):
            for source in sources:
                cache.request(broken_idat(source))
                self.assertTrue(cache.wait(timeout=5))
            self.assertEqual(len(cache._failed), 2)

    def test_root_symlink_and_symlink_ancestor_do_not_touch_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "link"
        self.link(link, outside, directory=True)
        for folder in ("link", "link/nested"):
            cache = self.cache(folder)
            self.assertIsNone(cache.request(png()))
            self.assertTrue(cache.wait(timeout=5))
            self.assertIsNone(cache.request(png()))
        self.assertEqual(list(outside.iterdir()), [])

    def test_cached_symlink_even_dangling_is_never_followed_or_replaced(self):
        source = png()
        outside = self.root / "outside.png"
        outside.write_bytes(source)
        for index, target in enumerate((outside, self.root / "missing.png")):
            cache = self.cache("links-" + str(index))
            cache.root.mkdir()
            link = cache.root / (backgrounds._cache_key(source) + ".png")
            self.link(link, target)
            self.assertIsNone(cache.request(source))
            self.assertTrue(cache.wait(timeout=5))
            self.assertIsNone(cache.request(source))
            self.assertTrue(link.is_symlink())
            self.assertEqual(list(cache.root.glob("*.tmp")), [])
        self.assertEqual(outside.read_bytes(), source)
        self.assertFalse((self.root / "missing.png").exists())

    def test_reparse_points_and_nonregular_files_are_rejected(self):
        for mode, directory in ((stat.S_IFDIR, True), (stat.S_IFREG, False)):
            info = SimpleNamespace(st_mode=mode, st_file_attributes=0x400)
            with self.assertRaises(OSError):
                backgrounds._regular(info, directory=directory)
        with self.assertRaises(OSError):
            backgrounds._regular(SimpleNamespace(st_mode=stat.S_IFIFO))
        blocked = self.root / "blocked"
        blocked.write_bytes(b"not a directory")
        cache = self.cache("blocked/child")
        cache.request(png())
        self.assertTrue(cache.wait(timeout=5))
        self.assertIsNone(cache.request(png()))
        self.assertEqual(blocked.read_bytes(), b"not a directory")

    def test_root_replaced_after_render_is_rejected_before_publication(self):
        cache = self.cache()
        source = png()
        outside = self.root / "outside"
        outside.mkdir()
        # Probe fixture privileges before the worker is patched.
        probe = self.root / "probe"
        self.link(probe, outside, directory=True)
        probe.unlink()
        real = backgrounds._render_background

        def replace_root(image, key):
            value = real(image, key)
            cache.root.rename(self.root / "old-cache")
            cache.root.symlink_to(outside, target_is_directory=True)
            return value

        with patch.object(backgrounds, "_render_background", side_effect=replace_root):
            cache.request(source)
            self.assertTrue(cache.wait(timeout=5))
            self.assertIsNone(cache.request(source))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list((self.root / "old-cache").iterdir()), [])

    def test_worker_start_failure_is_a_nonblocking_fallback(self):
        with patch("psp_dock.media.xmb_backgrounds.threading.Thread.start", side_effect=RuntimeError("no threads")):
            cache = self.cache()
        self.assertIsNone(cache.request(png()))
        self.assertTrue(cache.wait(timeout=0))
        cache.close(wait=False)
        cache.close(wait=True)


if __name__ == "__main__":
    unittest.main()
