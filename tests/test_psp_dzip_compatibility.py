"""Cross-title DZIP proof gates; synthetic data is never evidence of a game codec."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from psp_dock.emulation.psp_dzip_compatibility import (
    DzipProofError, DzipEnvelope, ReferenceCase, DecodedStream,
    compare_headers, parse_envelope, probe_game_iso, verify_cross_game_decoder,
)
from psp_dock.media.psp_artwork import SECTOR
from tests.test_psp_dariusburst_re import synthetic_dariusburst_iso
from tests.test_psp_game_metadata import fixture_sfo
from tests.test_psp_artwork import PNG, rec


def fixture_game(path: Path, disc_id: str):
    """One tiny original ISO with DZIP-like synthetic resources."""
    raw = bytearray(synthetic_dariusburst_iso(path))
    sfo = fixture_sfo({"DISC_ID": disc_id, "DISC_VERSION": "1.01",
                       "TITLE": "Fixture", "CATEGORY": "UG"})
    record = rec(b"PARAM.SFO;1", 23, len(sfo), False)
    original = rec(b"ICON0.PNG;1", 22, len(PNG), False)
    offset = 21 * SECTOR + len(original)
    raw[offset:offset + len(record)] = record
    raw[23 * SECTOR:24 * SECTOR] = sfo + bytes(SECTOR - len(sfo))
    path.write_bytes(raw)
    return bytes(raw)


def synthetic_block(data: bytes) -> bytes:
    return b"DZIP" + struct.pack("<I", len(data)) + zlib.compress(data)


def synthetic_zlib_decoder(payload: bytes, expected_size: int) -> DecodedStream:
    # This is a TEST-ONLY fake bitstream, not how Dariusburst DZIP decodes.
    z = zlib.decompressobj()
    decoded = z.decompress(payload, expected_size + 1)
    if not z.eof or z.unused_data or z.unconsumed_tail:
        raise DzipProofError("Datos comprimidos inconsistentes.")
    return DecodedStream(decoded, len(payload))


def reference_set() -> tuple[ReferenceCase, ...]:
    return tuple(ReferenceCase(game, f"texture-{index}.tx2",
                               synthetic_block(raw), raw, "emulator_runtime")
                 for game, index, raw in (
                     ("ULJM05558", 1, b"synthetic data A" * 3),
                     ("ULJM05558", 2, b"synthetic data B" * 8),
                     ("ULJM09999", 1, b"synthetic data C" * 9),
                     ("ULJM09999", 2, b"synthetic data D" * 2)))


class DzipCompatibilityTests(unittest.TestCase):
    def test_dzip_header_is_not_a_decoder_or_proof(self):
        packed = b"DZIP" + struct.pack("<I", 320) + b"\x84\x07\x60\xf2"
        envelope = parse_envelope(packed)
        self.assertEqual(envelope.declared_output_size, 320)
        self.assertEqual(envelope.unverified_prefix, "840760f2")
        self.assertEqual(envelope.size_bytes, len(packed))
        for malformed in (b"DZIP", b"DZIP" + bytes(8),
                          b"GZIP" + struct.pack("<I", 12) + b"A",
                          b"DZIP" + b"\xff"*4 + b"x"):
            with self.subTest(malformed=malformed):
                with self.assertRaises(DzipProofError):
                    parse_envelope(malformed)

    def test_two_isos_share_magic_without_proving_any_codec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "first.iso"
            b = root / "second.iso"
            before_a = fixture_game(a, "ULJM05558")
            before_b = fixture_game(b, "ULJM09999")
            left = probe_game_iso(a)
            right = probe_game_iso(b)
            self.assertEqual(left["container_count"], 3)
            self.assertEqual(right["container_count"], 3)
            self.assertEqual(left["extensions"], right["extensions"])
            report = compare_headers(left, right)
            self.assertTrue(report["same_magic"])
            self.assertFalse(report["decoder_verified"])
            self.assertFalse(report["cross_game_verified"])
            self.assertEqual(a.read_bytes(), before_a)
            self.assertEqual(b.read_bytes(), before_b)
            self.assertEqual(left["source_sha256"], hashlib.sha256(before_a).hexdigest())

    def test_a_single_game_is_never_cross_game_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.iso"
            fixture_game(first, "ULJM05558")
            report = probe_game_iso(first)
            with self.assertRaises(DzipProofError):
                compare_headers(report, report)
        samples = tuple(x for x in reference_set() if x.disc_id == "ULJM05558")
        with self.assertRaises(DzipProofError):
            verify_cross_game_decoder(samples, synthetic_zlib_decoder)

    def test_synthetic_codec_proof_checks_bytes_for_two_titles(self):
        result = verify_cross_game_decoder(reference_set(), synthetic_zlib_decoder)
        self.assertTrue(result["cross_game_verified_for_fixtures"])
        self.assertEqual(result["games"], ["ULJM05558", "ULJM09999"])
        self.assertEqual(result["samples_per_game"], {
            "ULJM05558": 2, "ULJM09999": 2})
        self.assertEqual(len(result["proof"]), 4)
        self.assertTrue(all(x["exact"] for x in result["proof"]))
        self.assertFalse(result["reference_provenance_independently_audited"])
        self.assertFalse(result["universal_game_compatibility"])
        self.assertFalse(result["automatic_texture_install_authorized"])

    def test_mismatch_or_unused_bytes_blocks_proof(self):
        cases = reference_set()
        wrong = list(cases)
        item = wrong[0]
        wrong[0] = ReferenceCase(item.disc_id, item.resource_name,
                                 item.packed, b"X"*len(item.reference_decoded),
                                 item.reference_kind)
        with self.assertRaises(DzipProofError):
            verify_cross_game_decoder(tuple(wrong), synthetic_zlib_decoder)

        def partial_decoder(payload, expected):
            result = synthetic_zlib_decoder(payload, expected)
            return DecodedStream(result.decoded, result.consumed_bytes - 1)
        with self.assertRaises(DzipProofError):
            verify_cross_game_decoder(cases, partial_decoder)

    def test_duplicate_or_unproved_fixtures_are_rejected(self):
        samples = reference_set()
        with self.assertRaises(DzipProofError):
            verify_cross_game_decoder(samples[:-1] + (samples[0],),
                                      synthetic_zlib_decoder)
        broken = list(samples)
        item = broken[0]
        broken[0] = ReferenceCase(item.disc_id, item.resource_name,
                                  item.packed, item.reference_decoded,
                                  "internet_guess")
        with self.assertRaises(DzipProofError):
            verify_cross_game_decoder(tuple(broken), synthetic_zlib_decoder)

    def test_wrong_dzip_output_size_is_not_acceptable(self):
        broken = list(reference_set())
        item = broken[1]
        broken[1] = ReferenceCase(item.disc_id, item.resource_name,
                                  item.packed, item.reference_decoded + b"X",
                                  item.reference_kind)
        with self.assertRaises(DzipProofError):
            verify_cross_game_decoder(tuple(broken), synthetic_zlib_decoder)

    def test_a_decoder_that_returns_only_bytes_is_not_accepted(self):
        with self.assertRaises(DzipProofError):
            verify_cross_game_decoder(
                reference_set(),
                lambda data, size: zlib.decompress(data))


if __name__ == "__main__":
    unittest.main()
