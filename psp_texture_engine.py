"""Deterministic, reusable PSP resource decoding pipeline.

The game identifies *where* files are, not *how* a supported format decodes.
The engine discovers formats by signature, routes only to tested algorithms,
and never asks a model to interpret files at runtime. Crucially, DZIP magic
does NOT imply a known codec: this module refuses that format until a genuine
reproducible decoder is implemented and validated with real test vectors.

Stages: identify envelope -> decompress -> decode raw GPU texture -> check
PPSSPP content hash -> optional exact provenance matching outside this module.
No stage publishes replacement filenames or writes to the PSP.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
import hashlib
from pathlib import Path
import re
import struct
import zlib

from ppsspp_texture_hash import ppsspp_content_hash


MAX_INPUT = 64 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
MAX_PIXELS = 16_777_216
ENGINE_VERSION = 1


class TextureEngineError(ValueError):
    pass


class UnsupportedCodec(TextureEngineError):
    pass


@dataclass(frozen=True)
class FormatDetection:
    family: str
    signature: str
    codec: str | None
    declared_size: int | None
    payload_offset: int
    status: str
    variant_prefix: str = ""
    # Fingerprints only: an observed prefix is NOT a proven codec version.

    def report(self) -> dict:
        return {**asdict(self), "engine_version": ENGINE_VERSION,
                "fully_decoded": False, "ppsspp_hash_verified": False}


def _zlib_header(raw: bytes) -> bool:
    return (len(raw) >= 2 and (raw[0] & 15) == 8 and
            (raw[0] >> 4) <= 7 and ((raw[0] << 8) | raw[1]) % 31 == 0)


def identify(raw: bytes, *, extension: str = "") -> FormatDetection:
    """Return a header assessment without interpreting extensions as codecs."""
    if not isinstance(raw, bytes) or len(raw) > MAX_INPUT:
        raise TextureEngineError("Entrada demasiado grande o inválida.")
    if not isinstance(extension, str) or len(extension) > 16:
        raise TextureEngineError("Extensión inválida.")
    if raw.startswith(b"DZIP"):
        if len(raw) < 9:
            return FormatDetection("dzip_unknown", "445a4950", None,
                                   None, 8, "malformed")
        declared = struct.unpack_from("<I", raw, 4)[0]
        if not 1 <= declared <= MAX_OUTPUT:
            return FormatDetection("dzip_unknown", "445a4950", None,
                                   None, 8, "malformed")
        return FormatDetection("dzip_unknown", "445a4950", None, declared,
                               8, "recognized_unsupported",
                               raw[8:12].hex())
    if raw.startswith(b"DTRZ"):
        # Marmalade's Dzip resource archives use a different magic and framing
        # from the PSP DZIP resources we observed in Dariusburst.
        return FormatDetection("marmalade_dzip", "4454525a", None,
                               None, 0, "recognized_unsupported")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return FormatDetection("png", "89504e470d0a1a0a", "identity",
                               len(raw), 0, "supported")
    if raw.startswith(b"\xff\xd8\xff"):
        return FormatDetection("jpeg", "ffd8ff", "identity",
                               len(raw), 0, "supported")
    if raw.startswith(b"\x1f\x8b\x08"):
        return FormatDetection("gzip", "1f8b08", "gzip",
                               None, 0, "supported")
    if _zlib_header(raw):
        return FormatDetection("zlib", raw[:2].hex(), "zlib",
                               None, 0, "supported")
    return FormatDetection("unknown", "", None, None, 0, "unrecognized")


CODEC_IDS = ("identity", "zlib", "gzip", "raw_deflate")


def decompress(payload: bytes, *, codec: str, expected_size: int) -> bytes:
    """Strict bounded decompressor. Raw deflate MUST be explicitly selected."""
    if not isinstance(payload, bytes) or not 0 <= len(payload) <= MAX_INPUT:
        raise TextureEngineError("La entrada comprimida excede el límite.")
    if type(expected_size) is not int or not 0 <= expected_size <= MAX_OUTPUT:
        raise TextureEngineError("Tamaño de salida no válido.")
    if codec not in CODEC_IDS:
        raise UnsupportedCodec("No existe un decodificador validado para este formato.")
    if codec == "identity":
        if len(payload) != expected_size:
            raise TextureEngineError("La longitud no coincide con el manifiesto.")
        return payload
    wbits = {"zlib": 15, "gzip": 31, "raw_deflate": -15}[codec]
    try:
        stream = zlib.decompressobj(wbits)
        decoded = stream.decompress(payload, expected_size + 1)
        if (not stream.eof or stream.unused_data or stream.unconsumed_tail
                or len(decoded) != expected_size):
            raise TextureEngineError(
                "Compresión inválida, datos sobrantes o longitud inesperada.")
        return decoded
    except zlib.error as exc:
        raise TextureEngineError("Flujo comprimido corrupto o incompatible.") from exc


@dataclass(frozen=True)
class CodecFixture:
    """An independently verified compressed/decoded pair (no game ID)."""
    packed: bytes
    decoded_sha256: str
    decoded_size: int
    sample: str


def validate_codec(codec: str, fixtures: tuple[CodecFixture, ...]) -> dict:
    """Regression proof for a reusable decoder, not automatic format inference.

    At least two different expected outputs are required; a matching length
    alone, a similar filename, or an unverified header is insufficient.
    """
    if codec not in CODEC_IDS or not 2 <= len(fixtures) <= 32:
        raise TextureEngineError("Faltan muestras independientes para validar.")
    names, digests = set(), set()
    for fixture in fixtures:
        if (not isinstance(fixture, CodecFixture)
                or not re.fullmatch("[0-9a-f]{64}", fixture.decoded_sha256)
                or not isinstance(fixture.sample, str)
                or not 1 <= len(fixture.sample) <= 100
                or fixture.sample in names or fixture.decoded_sha256 in digests):
            raise TextureEngineError("Prueba duplicada o sin SHA256 verificable.")
        result = decompress(fixture.packed, codec=codec,
                            expected_size=fixture.decoded_size)
        if hashlib.sha256(result).hexdigest() != fixture.decoded_sha256:
            raise TextureEngineError("El resultado no coincide con la referencia.")
        names.add(fixture.sample)
        digests.add(fixture.decoded_sha256)
    return {"codec": codec, "tested_samples": len(fixtures),
            "verified": True, "game_specific": False}


# Storage format decoding uses PSP's standard 16-bit channel ordering:
# low bits R, next G, then B, then A; 8888 bytes are RGBA on little-endian PSP.
_STORAGE_BPP = {
    "rgba8888": 32, "rgb565": 16, "rgba5551": 16, "rgba4444": 16,
    "clut4": 4, "clut8": 8,
}
_PALETTE_FORMATS = frozenset(("rgba8888", "rgb565", "rgba5551", "rgba4444"))


@dataclass(frozen=True)
class RawTextureSpec:
    width: int
    height: int
    buffer_width: int
    storage_format: str
    swizzled: bool = False
    palette_format: str = ""
    palette: bytes = b""

    def __post_init__(self):
        if (any(type(v) is not int for v in
                (self.width, self.height, self.buffer_width)) or
                not 1 <= self.width <= self.buffer_width <= 4096 or
                not 1 <= self.height <= 4096 or
                self.width * self.height > MAX_PIXELS or
                self.storage_format not in _STORAGE_BPP or
                type(self.swizzled) is not bool or
                not isinstance(self.palette, bytes)):
            raise TextureEngineError("Especificación gráfica GE inválida.")
        if self.storage_format.startswith("clut"):
            count = 16 if self.storage_format == "clut4" else 256
            if self.palette_format not in _PALETTE_FORMATS:
                raise TextureEngineError("La textura CLUT requiere formato de paleta.")
            bpp = _STORAGE_BPP[self.palette_format] // 8
            if len(self.palette) != count * bpp:
                raise TextureEngineError("La paleta CLUT no tiene el tamaño esperado.")
        elif self.palette or self.palette_format:
            raise TextureEngineError("Se recibió una paleta ajena al formato.")
        if (self.buffer_width * _STORAGE_BPP[self.storage_format]) % 8 != 0:
            raise TextureEngineError("El stride de bits no es un número de bytes.")

    @property
    def stride(self) -> int:
        return self.buffer_width * _STORAGE_BPP[self.storage_format] // 8

    @property
    def data_bytes(self) -> int:
        return self.stride * self.height


def _channel(value: int, bits: int) -> int:
    max_value = (1 << bits) - 1
    return (value * 255 + max_value // 2) // max_value


def _pixel(raw: bytes, offset: int, fmt: str) -> bytes:
    if fmt == "rgba8888":
        return raw[offset:offset + 4]
    n = int.from_bytes(raw[offset:offset + 2], "little")
    if fmt == "rgb565":
        return bytes((_channel(n & 31, 5),
                      _channel((n >> 5) & 63, 6),
                      _channel((n >> 11) & 31, 5), 255))
    if fmt == "rgba5551":
        return bytes((_channel(n & 31, 5),
                      _channel((n >> 5) & 31, 5),
                      _channel((n >> 10) & 31, 5),
                      255 if (n & 0x8000) else 0))
    if fmt == "rgba4444":
        return bytes((_channel(n & 15, 4),
                      _channel((n >> 4) & 15, 4),
                      _channel((n >> 8) & 15, 4),
                      _channel((n >> 12) & 15, 4)))
    raise TextureEngineError("Formato de píxel no admitido.")


def unswizzle(raw: bytes, *, stride: int, height: int) -> bytes:
    """Inverse PSP GE tiled address: 16-byte x 8-row blocks.

    Partial blocks are rejected rather than guessing padding; the PSP
    generally pads GPU textures to block-compatible buffer widths.
    """
    if (not isinstance(raw, bytes) or stride <= 0 or height <= 0 or
            stride % 16 or height % 8 or stride * height != len(raw)
            or stride * height > MAX_INPUT):
        raise TextureEngineError("Swizzle PSP requiere bloques completos 16x8.")
    blocks_per_row = stride // 16
    dst = bytearray(len(raw))
    for y in range(height):
        tile_y, intra_y = divmod(y, 8)
        for bx in range(blocks_per_row):
            source = (tile_y * blocks_per_row + bx) * 128 + intra_y * 16
            dest = y * stride + bx * 16
            dst[dest:dest + 16] = raw[source:source + 16]
    return bytes(dst)


@dataclass(frozen=True)
class DecodedTexture:
    width: int
    height: int
    rgba: bytes
    pixels_sha256: str
    ppsspp_content_hash_8: str
    # Critically, not the 24-hex filename; cachekey/palette are unknown.
    full_replacement_key_verified: bool = False

    def report(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "pixels_sha256": self.pixels_sha256,
            "ppsspp_content_hash_8": self.ppsspp_content_hash_8,
            "full_replacement_key_verified": False,
        }


def decode_raw_texture(raw: bytes, spec: RawTextureSpec) -> DecodedTexture:
    """Convert known GE raw bytes to exact visible RGBA pixels deterministically."""
    if not isinstance(spec, RawTextureSpec) or not isinstance(raw, bytes):
        raise TextureEngineError("Datos gráficos GE inválidos.")
    if len(raw) != spec.data_bytes or len(raw) > MAX_INPUT:
        raise TextureEngineError("Longitud de textura GE incoherente.")
    source = unswizzle(raw, stride=spec.stride, height=spec.height) if spec.swizzled else raw
    palette = []
    if spec.storage_format.startswith("clut"):
        palette_bpp = _STORAGE_BPP[spec.palette_format] // 8
        palette = [_pixel(spec.palette, n * palette_bpp, spec.palette_format)
                   for n in range(16 if spec.storage_format == "clut4" else 256)]
    result = bytearray(spec.width * spec.height * 4)
    for y in range(spec.height):
        start = y * spec.stride
        for x in range(spec.width):
            if spec.storage_format == "clut4":
                value = source[start + x // 2]
                index = (value >> (4 if x & 1 else 0)) & 15
                pixel = palette[index]
            elif spec.storage_format == "clut8":
                pixel = palette[source[start + x]]
            else:
                pixel = _pixel(source, start + x *
                               (_STORAGE_BPP[spec.storage_format] // 8),
                               spec.storage_format)
            destination = (y * spec.width + x) * 4
            result[destination:destination + 4] = pixel
    rgba = bytes(result)
    content_hash = ppsspp_content_hash(
        raw, buffer_width=spec.buffer_width, visible_width=spec.width,
        height=spec.height, bits_per_pixel=_STORAGE_BPP[spec.storage_format])
    return DecodedTexture(spec.width, spec.height, rgba,
                          hashlib.sha256(rgba).hexdigest(),
                          f"{content_hash:08x}")


@dataclass(frozen=True)
class ResourceResult:
    """Structured result of an asset; no LLM calls or hidden asset writes."""
    detected: FormatDetection
    status: str
    decoded_sha256: str | None
    texture: DecodedTexture | None
    reason: str = ""

    def report(self) -> dict:
        return {
            "engine_version": ENGINE_VERSION,
            "family": self.detected.family,
            "codec": self.detected.codec,
            "variant_prefix_unverified": self.detected.variant_prefix,
            "status": self.status,
            "reason": self.reason,
            "decoded_sha256": self.decoded_sha256,
            "texture": self.texture.report() if self.texture else None,
            "replacement_installable": False,
        }


def _decode_standard_image(raw: bytes, family: str) -> DecodedTexture:
    try:
        from PIL import Image
    except ImportError as exc:
        raise TextureEngineError("Pillow no está instalado.") from exc
    try:
        with Image.open(BytesIO(raw)) as source:
            width, height = source.size
            if (source.format != family.upper() and
                    not (family == "jpeg" and source.format == "JPEG")):
                raise TextureEngineError("La imagen interna no coincide con su firma.")
            if (width < 1 or height < 1 or
                    width * height > MAX_PIXELS or max(width, height) > 4096):
                raise TextureEngineError("Dimensiones de imagen no admitidas.")
            rgba = source.convert("RGBA").tobytes()
    except (OSError, ValueError) as exc:
        raise TextureEngineError("Imagen dañada o no admitida.") from exc
    return DecodedTexture(width, height, rgba,
                          hashlib.sha256(rgba).hexdigest(), "",
                          False)


def analyze_resource(raw: bytes, *, expected_size: int | None = None,
                     raw_texture: RawTextureSpec | None = None) -> ResourceResult:
    """Complete strict deterministic path, currently for known open codecs.

    DZIP is intentionally recognition-only until its precise bitstream is
    proven. A matching PNG payload, a plausible decompressed size, or the
    presence of a .tx2 extension cannot grant replacement installation.
    """
    detected = identify(raw)
    if detected.codec is None:
        return ResourceResult(detected, detected.status, None, None,
                              "No hay códec validado para esta familia.")
    if detected.codec == "identity":
        decoded = raw
    elif expected_size is None:
        return ResourceResult(detected, "needs_exact_size", None, None,
                              "Se necesita la longitud original verificada.")
    else:
        decoded = decompress(raw, codec=detected.codec, expected_size=expected_size)
    sha = hashlib.sha256(decoded).hexdigest()
    if raw_texture is not None:
        if not isinstance(raw_texture, RawTextureSpec):
            raise TextureEngineError("Metadatos gráficos inválidos.")
        texture = decode_raw_texture(decoded, raw_texture)
    else:
        internal_type = identify(decoded)
        if internal_type.family in ("png","jpeg"):
            texture = _decode_standard_image(decoded, internal_type.family)
        else:
            return ResourceResult(detected, "binary_only", sha, None,
                                  "Los bytes se han recuperado, pero no hay decodificador gráfico.")
    return ResourceResult(detected, "texture_decoded", sha, texture)


def inspect_file(path: Path) -> dict:
    """PC-only read, never executes a resource or opens images automatically."""
    file = Path(path)
    if not file.is_file() or file.is_symlink() or file.stat().st_size > MAX_INPUT:
        raise TextureEngineError("Recurso no accesible o demasiado grande.")
    with file.open("rb") as source:
        data = source.read()
    report = identify(data, extension=file.suffix).report()
    return {**report, "source_sha256": hashlib.sha256(data).hexdigest(),
            "input_bytes": len(data)}
