"""PPSSPP texture *content* hash reconstruction for decoded PSP RAM bytes.

TextureReplacer::ComputeHash() hashes raw PSP texture storage, NOT PNG pixels.
On the xxh64 setting, it truncates seeded XXH64 to 32 bits. To make a valid
replacement key, palette/clut cachekey and optional address must ALSO match.
This module cannot infer them, cannot decrypt or decompress DZIP and does not
publish an inferred replacement hash.

Algorithm reference: PPSSPP GPU/Common/TextureReplacer.cpp ComputeHash()
(xxh64 seed 0xBACD7814; bufw<=w contiguous else rowwise XOR with factor 11).
"""
from __future__ import annotations

U64 = (1 << 64) - 1
U32 = (1 << 32) - 1
P1 = 11400714785074694791
P2 = 14029467366897019727
P3 = 1609587929392839161
P4 = 9650029242287828579
P5 = 2870177450012600261
PPSSPP_SEED = 0xBACD7814


class TextureHashError(ValueError):
    pass


def rol64(x: int, shift: int) -> int:
    return ((x << shift) | (x >> (64 - shift))) & U64


def _round(acc: int, block: int) -> int:
    acc = (acc + block * P2) & U64
    acc = rol64(acc, 31)
    return (acc * P1) & U64


def _merge(acc: int, value: int) -> int:
    return (((acc ^ _round(0, value)) * P1) + P4) & U64


def xxh64(raw: bytes, seed: int = PPSSPP_SEED) -> int:
    """Pure Python XXH64 reference for deterministic offline verification."""
    if not isinstance(raw, bytes) or type(seed) is not int or not 0 <= seed <= U64:
        raise TextureHashError("Datos hash inválidos.")
    data = memoryview(raw)
    length = len(data)
    pos = 0
    if length >= 32:
        v1 = (seed + P1 + P2) & U64
        v2 = (seed + P2) & U64
        v3 = seed & U64
        v4 = (seed - P1) & U64
        while pos <= length - 32:
            v1 = _round(v1, int.from_bytes(data[pos:pos + 8], "little"))
            v2 = _round(v2, int.from_bytes(data[pos + 8:pos + 16], "little"))
            v3 = _round(v3, int.from_bytes(data[pos + 16:pos + 24], "little"))
            v4 = _round(v4, int.from_bytes(data[pos + 24:pos + 32], "little"))
            pos += 32
        result = (rol64(v1, 1) + rol64(v2, 7) +
                  rol64(v3, 12) + rol64(v4, 18)) & U64
        for v in (v1, v2, v3, v4):
            result = _merge(result, v)
    else:
        result = (seed + P5) & U64
    result = (result + length) & U64
    while pos + 8 <= length:
        block = _round(0, int.from_bytes(data[pos:pos + 8], "little"))
        result = ((rol64(result ^ block, 27) * P1) + P4) & U64
        pos += 8
    if pos + 4 <= length:
        result ^= (int.from_bytes(data[pos:pos + 4], "little") * P1) & U64
        result = ((rol64(result, 23) * P2) + P3) & U64
        pos += 4
    while pos < length:
        result ^= (data[pos] * P5) & U64
        result = (rol64(result, 11) * P1) & U64
        pos += 1
    result ^= result >> 33
    result = (result * P2) & U64
    result ^= result >> 29
    result = (result * P3) & U64
    result ^= result >> 32
    return result & U64


def ppsspp_content_hash(raw: bytes, *, buffer_width: int, visible_width: int,
                        height: int, bits_per_pixel: int,
                        reduce_hash_size: float = 1.0) -> int:
    """Replicate the memory hashing path of TextureReplacer::ComputeHash.

    Requires the raw *emulated PSP texture bytes* (including padding/stride),
    exact GE width/height/buffer-width and memory format. Does NOT attempt to
    guess parameters from a PNG or ignore CLUT/palette differences.
    """
    if not isinstance(raw, bytes):
        raise TextureHashError("No se pueden calcular hashes desde una imagen PNG.")
    if (any(type(v) is not int for v in
            (buffer_width, visible_width, height, bits_per_pixel)) or
            not 1 <= buffer_width <= 4096 or
            not 1 <= visible_width <= 4096 or
            not 1 <= height <= 4096 or
            bits_per_pixel not in (4, 8, 16, 32) or
            type(reduce_hash_size) not in (float, int) or
            reduce_hash_size != 1.0):
        # PPSSPP supports reduceHash but cannot be reproduced safely
        # without the exact pack's reduction configuration/hashes.
        raise TextureHashError("Metadatos GE desconocidos o reducción no admitida.")
    if buffer_width <= visible_width:
        total_pixels = buffer_width * height + (visible_width - buffer_width)
        checked_bytes = bits_per_pixel * total_pixels // 8
        if checked_bytes > len(raw):
            raise TextureHashError("Buffer de textura truncado.")
        return xxh64(raw[:checked_bytes]) & U32
    stride = bits_per_pixel * buffer_width // 8
    width_bytes = bits_per_pixel * visible_width // 8
    checked_span = (height - 1) * stride + width_bytes
    if checked_span > len(raw):
        raise TextureHashError("Buffer de textura estriado truncado.")
    result = 0
    for y in range(height):
        row = raw[y * stride:y * stride + width_bytes]
        result = ((result * 11) ^ (xxh64(row) & U32)) & U32
    return result


def display_content_hash(raw: bytes, **kwargs) -> str:
    """Eight hexadecimal digits: the last part of PPSSPP's 24-digit ID."""
    return f"{ppsspp_content_hash(raw, **kwargs):08x}"
