"""Synthetic codec and archive fixtures; no Sony audio is stored in tests."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import io
import struct
import unittest
from unittest.mock import patch
import wave

import psp_xmb_audio as audio


def frame(nibbles=(), *, predictor=0, shift=12, flags=0):
    values = list(nibbles) + [0] * (28 - len(nibbles))
    if len(values) != 28:
        raise ValueError("A synthetic frame needs at most 28 samples")
    payload = bytes((values[i] & 15) | ((values[i + 1] & 15) << 4)
                    for i in range(0, 28, 2))
    return bytes(((predictor << 4) | shift, flags)) + payload


def vag(*frames):
    payload = b"".join(frames)
    return (b"VAGp" + struct.pack(">4I", 4, 0, len(payload), 44100) +
            bytes(12) + b"SYNTHETIC-TEST".ljust(16, b"\0") + payload)


def pcm_from_wav(raw):
    with wave.open(io.BytesIO(raw), "rb") as reader:
        metadata = (reader.getnchannels(), reader.getsampwidth(),
                    reader.getframerate(), reader.getnframes(),
                    reader.getcomptype())
        pcm = reader.readframes(reader.getnframes())
    return metadata, list(struct.unpack("<" + "h" * (len(pcm) // 2), pcm))


def synthetic_profile():
    # Replaces every hash/offset in the profile with test-only generated data.
    rco = bytearray(b"SYNTHETIC RCO TEST".ljust(164, b"\0"))
    profiles = []
    for index, name in enumerate(audio.SOUND_NAMES):
        raw = vag(frame([index + 1, -(index + 1)]))
        profiles.append(audio._SoundProfile(name, len(rco), len(raw),
                                           hashlib.sha256(raw).hexdigest()))
        rco.extend(raw)
    raw_rco = bytes(rco)
    return raw_rco, tuple(profiles)


def patch_profile(raw_rco, profiles):
    stack = ExitStack()
    stack.enter_context(patch.object(audio, "RCO_SIZE", len(raw_rco)))
    stack.enter_context(patch.object(audio, "RCO_SHA256",
                                     hashlib.sha256(raw_rco).hexdigest()))
    stack.enter_context(patch.object(audio, "_SOUND_PROFILES", profiles))
    return stack


class VagCodecTests(unittest.TestCase):
    def decode(self, *frames):
        return pcm_from_wav(audio.decode_vag(vag(*frames)))[1]

    def test_signed_nibbles_low_before_high_and_canonical_wav(self):
        values = [1, -1, 7, -8, 2, -2, 0]
        raw = audio.decode_vag(vag(frame(values)))
        metadata, samples = pcm_from_wav(raw)
        self.assertEqual(metadata, (1, 2, 44100, 28, "NONE"))
        self.assertEqual(samples, values + [0] * 21)
        self.assertEqual(len(raw), 44 + 28 * 2)
        self.assertEqual(raw[:4], b"RIFF")
        self.assertEqual(struct.unpack_from("<I", raw, 4)[0], len(raw) - 8)
        self.assertEqual(raw[8:16], b"WAVEfmt ")
        self.assertEqual(raw[36:40], b"data")

    def test_signed_residual_scale_uses_arithmetic_shift(self):
        self.assertEqual(self.decode(frame([1, -1, 7, -8], shift=1))[:4],
                         [2048, -2048, 14336, -16384])

    def test_negative_predictor_division_truncates_toward_zero(self):
        seed = frame([0] * 27 + [-3])
        self.assertEqual(self.decode(seed, frame(predictor=1))[28:33],
                         [-2, -1, 0, 0, 0])

    def test_prediction_is_truncated_before_adding_signed_residual(self):
        seed = frame([0] * 27 + [-3])
        self.assertEqual(self.decode(seed, frame([3], predictor=1))[28], 1)

    def test_second_order_predictor_and_cross_block_history(self):
        seed = frame([0] * 26 + [2, -3])
        self.assertEqual(self.decode(seed, frame(predictor=2))[28:33],
                         [-7, -10, -12, -13, -13])

    def test_positive_clipping_does_not_clip_prediction_history(self):
        seed = frame([0] * 27 + [7], shift=0)
        result = self.decode(seed, frame([7, -8], predictor=1, shift=0))
        self.assertEqual(result[28:30], [32767, 19312])

    def test_negative_clipping_does_not_clip_prediction_history(self):
        seed = frame([0] * 27 + [-8], shift=0)
        result = self.decode(seed, frame([-8, 7], predictor=1, shift=0))
        self.assertEqual(result[28:30], [-32768, -30848])

    def test_flag_seven_is_silence_and_resets_history(self):
        result = self.decode(frame([7] * 28, shift=0),
                             frame([7] * 28, predictor=4, shift=0, flags=7),
                             frame(predictor=1))
        self.assertEqual(result[28:], [0] * 56)

    def test_end_and_loop_flags_decode_once_without_trimming(self):
        result = self.decode(frame([2], flags=1), frame([-2], flags=6))
        self.assertEqual(len(result), 56)
        self.assertEqual((result[0], result[28]), (2, -2))

    def test_invalid_headers_and_input_sizes(self):
        good = vag(frame())
        cases = [b"", bytes(48), bytearray(good), good[:-1], good + b"\0",
                 good[:48], bytes(audio.MAX_VAG_BYTES + 1)]
        for offset, raw in ((0, b"NOPE"), (4, struct.pack(">I", 0x20)),
                            (8, struct.pack(">I", 1)),
                            (12, struct.pack(">I", 32)),
                            (16, struct.pack(">I", 48000)),
                            (20, b"\1")):
            changed = bytearray(good)
            changed[offset:offset + len(raw)] = raw
            cases.append(bytes(changed))
        for data in cases:
            with self.subTest(size=len(data)), self.assertRaises(audio.XmbAudioError):
                audio.decode_vag(data)

    def test_invalid_block_controls_reject_the_complete_stream(self):
        for invalid in (frame(predictor=5), frame(shift=13), frame(flags=8)):
            with self.subTest(control=invalid[:2]), self.assertRaises(audio.XmbAudioError):
                audio.decode_vag(vag(frame(), invalid))

    def test_pcm_bound_is_checked_before_allocating_output(self):
        with patch.object(audio, "MAX_PCM_BYTES", 55):
            with self.assertRaises(audio.XmbAudioError):
                audio.decode_vag(vag(frame()))


class RcoProfileTests(unittest.TestCase):
    def test_synthetic_profile_returns_all_four_canonical_wavs(self):
        raw, profiles = synthetic_profile()
        with patch_profile(raw, profiles):
            results = audio.extract_sounds(raw)
        self.assertEqual(tuple(results), audio.SOUND_NAMES)
        for index, name in enumerate(audio.SOUND_NAMES):
            metadata, samples = pcm_from_wav(results[name])
            self.assertEqual(metadata, (1, 2, 44100, 28, "NONE"))
            self.assertEqual(samples[:2], [index + 1, -(index + 1)])

    def test_unknown_or_changed_archive_is_rejected_before_decode(self):
        raw, profiles = synthetic_profile()
        changed = raw[:-1] + bytes((raw[-1] ^ 1,))
        with patch_profile(raw, profiles), patch.object(audio, "decode_vag") as decode:
            for bad in (raw[:-1], raw + b"\0", changed, bytearray(raw)):
                with self.subTest(size=len(bad)), self.assertRaises(audio.XmbAudioError):
                    audio.extract_sounds(bad)
            decode.assert_not_called()

    def test_original_profile_does_not_accept_synthetic_archive(self):
        raw, _ = synthetic_profile()
        with self.assertRaises(audio.XmbAudioError):
            audio.extract_sounds(raw)

    def test_per_sound_hash_is_required_even_after_archive_hash_match(self):
        raw, profiles = synthetic_profile()
        changed = profiles[:-1] + (replace(profiles[-1], sha256="0" * 64),)
        with patch_profile(raw, changed), patch.object(audio, "decode_vag") as decode:
            with self.assertRaises(audio.XmbAudioError):
                audio.extract_sounds(raw)
            decode.assert_not_called()

    def test_sound_profile_bounds_are_checked(self):
        raw, profiles = synthetic_profile()
        for altered in (replace(profiles[0], offset=-1),
                        replace(profiles[0], offset=163),
                        replace(profiles[0], offset=len(raw)),
                        replace(profiles[0], size=0),
                        replace(profiles[0], size=audio.MAX_VAG_BYTES + 1)):
            with self.subTest(altered=altered):
                with patch_profile(raw, (altered,) + profiles[1:]):
                    with self.assertRaises(audio.XmbAudioError):
                        audio.extract_sounds(raw)

    def test_missing_duplicate_or_reordered_entries_are_not_partial_success(self):
        raw, profiles = synthetic_profile()
        for altered in (profiles[:-1], profiles[::-1],
                        (replace(profiles[0], name="cancel"),) + profiles[1:]):
            with patch_profile(raw, altered), self.assertRaises(audio.XmbAudioError):
                audio.extract_sounds(raw)

    def test_malformed_sound_is_rejected_even_with_synthetic_hash_pins(self):
        raw, profiles = synthetic_profile()
        bad = bytearray(raw)
        bad[profiles[-1].offset + 48] = 0x5C
        entry = profiles[-1]
        replacement = replace(entry, sha256=hashlib.sha256(
            bad[entry.offset:entry.offset + entry.size]).hexdigest())
        with patch_profile(bytes(bad), profiles[:-1] + (replacement,)):
            with self.assertRaises(audio.XmbAudioError):
                audio.extract_sounds(bytes(bad))


if __name__ == "__main__":
    unittest.main()
