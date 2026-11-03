"""Import the four supported XMB sounds from a user's own PSP resource.

No firmware or audio payload is distributed by this module.  The only accepted
archive profile is the verified, stock PSP 6.61 non-Go system_plugin.rco.  Its
offsets were obtained from the named RCO sound entries (snd_cursor, snd_decide,
snd_cancel, snd_option), not from a signature scan.  The entire archive and each
VAG slice must match the profile before any PCM is returned.

The RCO directory is RLZ-compressed but these VAG payloads are not.  A fixed,
hash-gated profile therefore needs no RLZ library, firmware downloader, external
decoder, or native executable.  Unknown firmware/theme resources fail closed.

Format references used for this independent implementation:
- https://github.com/kakaroto/RCOMage/blob/54ca649a9a6aba150a1fbe423f4c3aec611ee913/src/rcofile.h
- https://github.com/vgmstream/vgmstream/blob/master/src/meta/vag.c
- https://ffmpeg.org/doxygen/trunk/adpcm_8c_source.html (PS-ADPCM arithmetic)

PS-ADPCM is decoded once, without looping, resampling, gain changes or trimming.
In particular, the known PSP version-4 resource is mono; it must not be treated
as a PS2 stereo VAG merely because of its version field.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import struct
import wave


SOURCE_RELATIVE = "PSP/SYSTEM/PSPDock/xmb/system_plugin.rco"
RCO_SIZE = 89012
RCO_SHA256 = "183ec1bf1076ce8fa77ce83e5b94b3bf0f043a73b7db37205bb32cdedb27adb3"
SOUND_NAMES = ("cursor", "decide", "cancel", "option")

# Keep even standalone decoder calls bounded to short, mono UI effects.
VAG_HEADER_SIZE = 48
ADPCM_BLOCK_SIZE = 16
SAMPLES_PER_BLOCK = 28
MAX_BLOCKS = 1024
MAX_VAG_BYTES = VAG_HEADER_SIZE + MAX_BLOCKS * ADPCM_BLOCK_SIZE
MAX_PCM_BYTES = MAX_BLOCKS * SAMPLES_PER_BLOCK * 2
SAMPLE_RATE = 44100

# Standard PlayStation ADPCM predictors, scaled by 64.
_PREDICTORS = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))


class XmbAudioError(ValueError):
    """Unsupported, malformed, or altered device audio resource."""


@dataclass(frozen=True)
class _SoundProfile:
    name: str
    offset: int
    size: int
    sha256: str


# Metadata only: no original waveform, compressed audio or RCO bytes.
_SOUND_PROFILES = (
    _SoundProfile("cursor", 55412, 1104,
                  "ec0ba776ff4a3bf396be5c2f7db534a9c2305be7156b4ba87a91c766ae1a41fc"),
    _SoundProfile("decide", 56516, 1104,
                  "115c10b72dd13a9160ed02d7679f10bd77ecea60922a1f99418705f63b0ba8ee"),
    _SoundProfile("cancel", 57620, 1104,
                  "cb91402979b01225954d38ad82a1383405945962979939e11a1b7285f88b9cd7"),
    _SoundProfile("option", 59828, 1104,
                  "5a4f423481ee23dc5abca16ce2465b15a2ea3b14ba51f8efde76dee594d86cd7"),
)


def _divide_by_64(value: int) -> int:
    # C-style integer division, including negative predictions.  Python's //
    # rounds negative values down, which changes the decoded PCM.
    return value // 64 if value >= 0 else -((-value) // 64)


def decode_vag(vag: bytes) -> bytes:
    """Return canonical mono PCM16 WAV bytes for the supported PSP VAG format.

    This bounded codec accepts a version-4 mono PSP resource at 44.1 kHz.
    Channel interpretation is supplied by the verified RCO profile, not by a
    guessed stereo layout.  It raises XmbAudioError for malformed/unsupported
    data and performs no filesystem or network operations.
    """
    if not isinstance(vag, bytes):
        raise XmbAudioError("VAG input must be immutable bytes")
    if not VAG_HEADER_SIZE + ADPCM_BLOCK_SIZE <= len(vag) <= MAX_VAG_BYTES:
        raise XmbAudioError("VAG size is outside the supported limit")
    if vag[:4] != b"VAGp":
        raise XmbAudioError("Invalid VAG signature")

    version, reserved, payload_size, rate = struct.unpack_from(">4I", vag, 4)
    if version != 4 or reserved != 0 or any(vag[20:32]):
        raise XmbAudioError("Unsupported PSP VAG header")
    if rate != SAMPLE_RATE:
        raise XmbAudioError("Unsupported PSP VAG sample rate")
    if payload_size != len(vag) - VAG_HEADER_SIZE or payload_size % ADPCM_BLOCK_SIZE:
        raise XmbAudioError("VAG payload size does not match its complete blocks")

    block_count = payload_size // ADPCM_BLOCK_SIZE
    pcm_size = block_count * SAMPLES_PER_BLOCK * 2
    if not 0 < pcm_size <= MAX_PCM_BYTES:
        raise XmbAudioError("Decoded PCM would exceed the supported limit")

    # Validate every block before allocating/decoding output.  Unsupported
    # predictor, shift or flag bits must not silently become a different sound.
    for offset in range(VAG_HEADER_SIZE, len(vag), ADPCM_BLOCK_SIZE):
        control, flags = vag[offset], vag[offset + 1]
        if (control >> 4) >= len(_PREDICTORS) or (control & 15) > 12:
            raise XmbAudioError("Unsupported PS-ADPCM predictor or shift")
        if flags & ~7:
            raise XmbAudioError("Unsupported PS-ADPCM flag bits")

    pcm = bytearray(pcm_size)
    previous = second_previous = 0
    write_offset = 0
    for offset in range(VAG_HEADER_SIZE, len(vag), ADPCM_BLOCK_SIZE):
        control, flags = vag[offset], vag[offset + 1]
        shift = control & 15
        first_coefficient, second_coefficient = _PREDICTORS[control >> 4]
        for packed in vag[offset + 2:offset + ADPCM_BLOCK_SIZE]:
            for nibble in (packed & 15, packed >> 4):
                if flags == 7:
                    sample = 0
                else:
                    signed = nibble - 16 if nibble & 8 else nibble
                    residual = (signed * 4096) >> shift
                    prediction = _divide_by_64(
                        previous * first_coefficient +
                        second_previous * second_coefficient
                    )
                    sample = residual + prediction
                # Prediction history retains its unclipped value; the PCM
                # output saturates to signed 16-bit.  This also handles flag-7
                # silent blocks without interpreting their padding as sound.
                second_previous, previous = previous, sample
                struct.pack_into("<h", pcm, write_offset,
                                 max(-32768, min(32767, sample)))
                write_offset += 2

    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(pcm)
    return output.getvalue()


def extract_sounds(rco: bytes) -> dict[str, bytes]:
    """Extract cursor/decide/cancel/option WAVs from the one verified RCO.

    Size and SHA-256 are checked before using fixed offsets.  All slices are
    validated before decoding.  A caller receives all four effects or an
    exception, never a partial result from a damaged/unknown archive.
    """
    if not isinstance(rco, bytes):
        raise XmbAudioError("RCO input must be immutable bytes")
    if len(rco) != RCO_SIZE:
        raise XmbAudioError("Unsupported system_plugin.rco size")
    if hashlib.sha256(rco).hexdigest() != RCO_SHA256:
        raise XmbAudioError("Unsupported or changed system_plugin.rco")
    if tuple(item.name for item in _SOUND_PROFILES) != SOUND_NAMES:
        raise XmbAudioError("Invalid supported sound profile")

    verified: dict[str, bytes] = {}
    for item in _SOUND_PROFILES:
        if (item.offset < 164 or
                not VAG_HEADER_SIZE + ADPCM_BLOCK_SIZE <= item.size <= MAX_VAG_BYTES or
                item.offset > len(rco) - item.size):
            raise XmbAudioError("Sound profile points outside the archive")
        vag = rco[item.offset:item.offset + item.size]
        if hashlib.sha256(vag).hexdigest() != item.sha256:
            raise XmbAudioError("Sound does not match the supported profile")
        verified[item.name] = vag
    return {name: decode_vag(vag) for name, vag in verified.items()}
