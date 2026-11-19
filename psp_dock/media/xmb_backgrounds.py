"""Asynchronous, PC-only enlargement of the user's original XMB PNGs.

This is deterministic Pillow resampling, not generative reconstruction. The
caller supplies immutable PNG bytes; this module never opens source paths.
request() only checks a bounded header, hashes bytes and touches in-memory state.
Decoding, disk reads and writes, resizing and sharpening run on one daemon worker.

A cold disk hit is asynchronous too. Poll request() again after a miss, retaining
the original image as fallback. Four jobs may wait behind one active job. Ready
bytes and failed keys are bounded; failures may be retried after 30 seconds.
close(wait=False) stops new requests and lets accepted jobs finish in the
background. close(wait=True) additionally joins; wait() drains without closing.
"""
from __future__ import annotations

from collections import OrderedDict, deque
import hashlib
import io
import math
import os
from pathlib import Path
import stat
import struct
import tempfile
import threading
import time
import zlib

from PIL import Image, ImageChops, ImageFilter, PngImagePlugin


ALGORITHM_VERSION = "pillow-lanczos-soft-v3"
TARGET_WIDTH = 3840
MAX_EDGE = 4096
MAX_INPUT_PIXELS = 8_000_000
MAX_OUTPUT_PIXELS = 10_000_000
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_QUEUED = 4
MAX_READY_ITEMS = 8
MAX_READY_BYTES = 40 * 1024 * 1024
MAX_FAILED_KEYS = 64
RETRY_SECONDS = 30.0
SHARPEN_MAX_DELTA = 6
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_KEY_TAG = "PSPDockBackgroundKey"


class _InvalidPNG(ValueError):
    pass


class _UnsafeCache(OSError):
    pass


def _cache_key(source: bytes) -> str:
    digest = hashlib.sha256(ALGORITHM_VERSION.encode("ascii") + b"\0")
    digest.update(source)
    return digest.hexdigest()


def _header_size(data: bytes, max_bytes: int, max_pixels: int) -> tuple[int, int]:
    if (not isinstance(data, bytes) or not 45 <= len(data) <= max_bytes or
            data[:8] != _PNG_SIGNATURE or data[8:16] != b"\0\0\0\rIHDR"):
        raise _InvalidPNG("Invalid or oversized PNG.")
    width, height = struct.unpack_from(">II", data, 16)
    if not (0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE and
            width * height <= max_pixels):
        raise _InvalidPNG("PNG dimensions exceed limits.")
    return width, height


def _check_chunks(data: bytes) -> None:
    # Check every CRC, including IEND (which Pillow's verify() may skip).
    # Reject trailing payloads and APNG streams before pixel allocation.
    offset = 8
    view = memoryview(data)
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + length + 12
        if end > len(data) or kind == b"acTL":
            raise _InvalidPNG("Truncated or animated PNG.")
        expected_crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(view[offset + 4:end - 4]) & 0xffffffff != expected_crc:
            raise _InvalidPNG("PNG chunk checksum does not match.")
        if kind == b"IEND":
            if length or end != len(data):
                raise _InvalidPNG("Invalid PNG ending.")
            return
        offset = end
    raise _InvalidPNG("Missing PNG ending.")


def _decode_png(data: bytes, *, max_bytes: int, max_pixels: int,
                expected_size: tuple[int, int] | None = None,
                key: str | None = None) -> Image.Image:
    size = _header_size(data, max_bytes, max_pixels)
    if expected_size is not None and size != expected_size:
        raise _InvalidPNG("Cached dimensions do not match the source.")
    _check_chunks(data)
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "PNG" or image.size != size:
            raise _InvalidPNG("Not a PNG image.")
        if key is not None and image.info.get(_KEY_TAG) != key:
            raise _InvalidPNG("Cached image belongs to another source or algorithm.")
        image.verify()
    # verify() checks the container; load() separately proves the IDAT stream
    # actually decodes. Do not change Pillow's process-wide safety settings.
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        if image.mode.startswith("I;16") or image.mode == "I":
            gray = image.convert("I")
            result = gray.point(lambda value: value / 257).convert("RGB")
            if has_alpha:
                # Pillow's I;16 -> RGBA conversion loses a 16-bit tRNS key.
                # Build its mask at the original precision before downconversion.
                transparent = image.info.get("transparency")
                if not isinstance(transparent, int) or not 0 <= transparent <= 65535:
                    raise _InvalidPNG("Invalid grayscale transparency key.")
                alpha = [255] * 65536
                alpha[transparent] = 0
                result.putalpha(gray.point(alpha, mode="L"))
        else:
            result = image.convert("RGBA" if has_alpha else "RGB")
        # Do not copy EXIF, compressed text or other untrusted metadata.
        result.info.clear()
        return result


def _output_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    scale = min(TARGET_WIDTH / width, MAX_EDGE / width, MAX_EDGE / height,
                math.sqrt(MAX_OUTPUT_PIXELS / (width * height)))
    # Floor both axes using the same scale; the epsilon only removes floating
    # point noise at an exact integer. Very thin images still need one pixel.
    result = (max(1, math.floor(width * scale + 1e-9)),
              max(1, math.floor(height * scale + 1e-9)))
    if max(result) > MAX_EDGE or result[0] * result[1] > MAX_OUTPUT_PIXELS:
        raise _InvalidPNG("Output dimensions exceed limits.")
    return result


def _soft_sharpen(rgb: Image.Image) -> Image.Image:
    candidate = rgb.filter(ImageFilter.UnsharpMask(radius=0.8, percent=40, threshold=2))
    lower = rgb.point([max(0, value - SHARPEN_MAX_DELTA) for value in range(256)] * 3)
    upper = rgb.point([min(255, value + SHARPEN_MAX_DELTA) for value in range(256)] * 3)
    return ImageChops.lighter(lower, ImageChops.darker(upper, candidate))


class _BoundedPNG(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_OUTPUT_BYTES:
            raise _InvalidPNG("Encoded background exceeds its byte limit.")
        return super().write(data)


def _render_background(source: Image.Image, key: str) -> bytes:
    size = _output_size(source.size)
    if source.mode == "RGBA":
        # Premultiplied resizing prevents hidden transparent RGB from bleeding
        # into visible edges. Sharpen only colour; retain the resized alpha.
        resized = source.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")
        result = _soft_sharpen(resized.convert("RGB"))
        result.putalpha(resized.getchannel("A"))
    else:
        result = _soft_sharpen(source.resize(size, Image.Resampling.LANCZOS))
    result.info.clear()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text(_KEY_TAG, key)
    with _BoundedPNG() as stream:
        result.save(stream, format="PNG", pnginfo=metadata, compress_level=6)
        return stream.getvalue()


def _regular(info: os.stat_result, *, directory: bool = False) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise _UnsafeCache("Background cache cannot follow links or reparse points.")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise _UnsafeCache("Background cache has a nonregular path.")


class BackgroundCache:
    """Bounded asynchronous cache; root must be a local PC directory.

    request(source_png: bytes) -> bytes | None
    wait(timeout: float | None = None) -> bool
    close(wait: bool = False) -> None

    A closed instance returns None for every request. Accepted work is drained
    even when close does not wait. Filesystem failures remain a None fallback.
    """

    def __init__(self, root: str | Path):
        supplied = Path(root)
        self.root = supplied.absolute()
        self._invalid_root = ".." in supplied.parts or str(supplied).startswith(("\\\\", "//"))
        self._root_identity: tuple[int, int] | None = None
        self._condition = threading.Condition()
        self._jobs: deque[tuple[str, bytes]] = deque()
        self._pending: set[str] = set()
        self._ready: OrderedDict[str, bytes] = OrderedDict()
        self._ready_bytes = 0
        self._failed: OrderedDict[str, float] = OrderedDict()
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="xmb-backgrounds", daemon=True)
        try:
            self._worker.start()
        except (OSError, RuntimeError):
            self._closed = True

    def request(self, source_png: bytes) -> bytes | None:
        """Return ready bytes, or schedule one job without waiting for I/O."""
        try:
            _header_size(source_png, MAX_INPUT_BYTES, MAX_INPUT_PIXELS)
        except (ValueError, TypeError, struct.error):
            return None
        key = _cache_key(source_png)
        with self._condition:
            if self._closed:
                return None
            ready = self._ready.get(key)
            if ready is not None:
                self._ready.move_to_end(key)
                return ready
            if key in self._pending or self._failed.get(key, 0) > time.monotonic():
                return None
            if len(self._jobs) >= MAX_QUEUED:
                return None
            self._failed.pop(key, None)
            self._pending.add(key)
            self._jobs.append((key, source_png))
            self._condition.notify_all()
        return None

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for currently accepted work, including failed jobs, to finish."""
        with self._condition:
            return self._condition.wait_for(lambda: not self._pending, timeout=timeout)

    def close(self, wait: bool = False) -> None:
        """Stop admission; optionally wait until all accepted work finishes."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if wait and self._worker.ident is not None and threading.current_thread() is not self._worker:
            self._worker.join()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._jobs or self._closed)
                if not self._jobs:
                    return
                key, source = self._jobs.popleft()
            try:
                value = self._build(key, source)
            except Exception:
                # Optional artwork must not break navigation or kill the worker.
                value = None
            with self._condition:
                if value is not None:
                    self._ready[key] = value
                    self._ready_bytes += len(value)
                    while (len(self._ready) > MAX_READY_ITEMS or
                           self._ready_bytes > MAX_READY_BYTES):
                        _, removed = self._ready.popitem(last=False)
                        self._ready_bytes -= len(removed)
                else:
                    self._failed[key] = time.monotonic() + RETRY_SECONDS
                    while len(self._failed) > MAX_FAILED_KEYS:
                        self._failed.popitem(last=False)
                self._pending.discard(key)
                self._condition.notify_all()

    def _safe_root(self, *, create: bool) -> None:
        if self._invalid_root:
            raise _UnsafeCache("Background cache needs a local path without traversal.")
        for folder in reversed((self.root, *self.root.parents)):
            try:
                info = folder.lstat()
            except FileNotFoundError:
                if not create:
                    raise
                folder.mkdir(mode=0o700, exist_ok=True)
                info = folder.lstat()
            _regular(info, directory=True)
        identity = (info.st_dev, info.st_ino)
        if self._root_identity is not None and identity != self._root_identity:
            raise _UnsafeCache("Background cache root was replaced.")
        self._root_identity = identity

    def _read_cached(self, key: str, size: tuple[int, int], mode: str) -> bytes | None:
        self._safe_root(create=False)
        path = self.root / (key + ".png")
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        _regular(info)
        if not 45 <= info.st_size <= MAX_OUTPUT_BYTES:
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            _regular(opened)
            if not os.path.samestat(info, opened):
                raise _UnsafeCache("Cached background changed while opening.")
            self._safe_root(create=False)
            _regular(path.lstat())
            value = stream.read(MAX_OUTPUT_BYTES + 1)
        try:
            with _decode_png(value, max_bytes=MAX_OUTPUT_BYTES, max_pixels=MAX_OUTPUT_PIXELS,
                             expected_size=size, key=key) as image:
                return value if image.mode == mode else None
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            return None

    def _publish(self, key: str, value: bytes) -> None:
        self._safe_root(create=False)
        target = self.root / (key + ".png")
        try:
            _regular(target.lstat())
        except FileNotFoundError:
            pass
        stage: Path | None = None
        stage_info = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".xmb-background-", suffix=".tmp", dir=self.root)
            stage = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stage_info = os.fstat(stream.fileno())
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            self._safe_root(create=False)
            current = stage.lstat()
            _regular(current)
            if not os.path.samestat(stage_info, current):
                raise _UnsafeCache("Background staging file was replaced.")
            try:
                _regular(target.lstat())
            except FileNotFoundError:
                pass
            os.replace(stage, target)
        finally:
            if stage is not None:
                try:
                    self._safe_root(create=False)
                    current = stage.lstat()
                    _regular(current)
                    if stage_info is not None and os.path.samestat(stage_info, current):
                        stage.unlink()
                except OSError:
                    pass

    def _build(self, key: str, source: bytes) -> bytes:
        with _decode_png(source, max_bytes=MAX_INPUT_BYTES, max_pixels=MAX_INPUT_PIXELS) as image:
            self._safe_root(create=True)
            value = self._read_cached(key, _output_size(image.size), image.mode)
            if value is None:
                value = _render_background(image, key)
                self._publish(key, value)
            return value
