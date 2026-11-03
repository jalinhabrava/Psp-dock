"""Dariusburst reverse-engineering baseline: no proprietary fixtures or ROM."""
import hashlib
from pathlib import Path
import struct
import tempfile
import time
import unittest

from dock import Dock
from psp_artwork import SECTOR
from psp_dariusburst_re import (
    DariusburstREError, DzipHeader, GAME_DISC_ID, inventory_dariusburst,
    safe_report_write, tx2_size_hypotheses, capture_inventory,
    compare_capture_sessions, correlate_decoded_candidates,
)
from tests.test_psp_artwork import PNG, rec, write_fixture_cso, write_fixture_iso
from tests.test_psp_game_metadata import fixture_sfo

try:
    from PIL import Image
except ImportError:
    Image = None


def synthetic_dariusburst_iso(path: Path):
    """Create a tiny, original synthetic ISO with DZIP *headers* only."""
    write_fixture_iso(path)
    raw = bytearray(path.read_bytes())
    sfo = fixture_sfo({
        "DISC_ID": GAME_DISC_ID, "DISC_VERSION": "1.01",
        "TITLE": "Dariusburst", "CATEGORY": "UG",
    })
    psp_entries = [
        rec(b"ICON0.PNG;1", 22, len(PNG), False),
        rec(b"PARAM.SFO;1", 23, len(sfo), False),
        rec(b"USRDIR", 24, SECTOR, True),
    ]
    raw[21 * SECTOR:22 * SECTOR] = b"\0" * SECTOR
    raw[21 * SECTOR:21 * SECTOR + len(b"".join(psp_entries))] = b"".join(psp_entries)
    raw[23 * SECTOR:23 * SECTOR + len(sfo)] = sfo
    dirs = [
        rec(b"IMAGES", 25, SECTOR, True),
        rec(b"SCENES", 27, SECTOR, True),
        rec(b"PARTICLES", 29, SECTOR, True),
    ]
    raw[24 * SECTOR:24 * SECTOR + len(b"".join(dirs))] = b"".join(dirs)
    specs = [
        (25, 26, b"ILLUST01.TX2;1", 263208),
        (27, 28, b"AZAKO010.XXI;1", 1200),
        (29, 30, b"PARTICLE0001.BXN;1", 300),
    ]
    for directory_sector, data_sector, name, declared in specs:
        payload = b"DZIP" + struct.pack("<I", declared) + b"synthetic compressed body"
        record = rec(name, data_sector, len(payload), False)
        raw[directory_sector * SECTOR:directory_sector * SECTOR + len(record)] = record
        raw[data_sector * SECTOR:data_sector * SECTOR + len(payload)] = payload
    path.write_bytes(raw)
    return bytes(raw)


class DariusburstRETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.iso = self.root / "game.iso"
        self.source = synthetic_dariusburst_iso(self.iso)

    def test_dzip_envelope_is_not_a_decompressor(self):
        self.assertEqual(DzipHeader.inspect(
            b"DZIP" + struct.pack("<I", 200), 60).unpacked_size, 200)
        for header in (b"ZLIB\x00\x00\x00\x00", b"DZIP" + b"\xff" * 4,
                       b"DZIP\x00\x00\x00\x00"):
            with self.assertRaises(DariusburstREError):
                DzipHeader.inspect(header, 60)
        hypothesis = tx2_size_hypotheses(263208)
        self.assertTrue(any([512, 512] in item["possible_dimensions"]
                            for item in hypothesis))
        self.assertFalse(tx2_size_hypotheses(7))

    def test_realistic_structure_and_sha_provenance_without_game_assets(self):
        report = inventory_dariusburst(self.iso)
        self.assertEqual(report["file_count"], 5)
        self.assertEqual(report["dzip_count"], 3)
        self.assertEqual(report["disc_id"], GAME_DISC_ID)
        self.assertEqual(report["disc_version"], "1.01")
        self.assertEqual(report["total_declared_unpacked_bytes"],
                         263208 + 1200 + 300)
        self.assertEqual(report["extension_counts"][".tx2"], 1)
        self.assertEqual(report["group_counts"]["SCENES"], 1)
        self.assertEqual(len(report["errors"]), 0)
        self.assertFalse(report["decompression_verified"])
        self.assertFalse(report["replacement_hashes_derived"])
        self.assertEqual(report["source_sha256"],
                         hashlib.sha256(self.source).hexdigest())
        self.assertEqual(self.iso.read_bytes(), self.source)
        self.assertTrue(all(asset["ppsspp_hash"] is None for asset in report["records"]))
        self.assertTrue(all("packed_sha256" in asset for asset in report["records"]))

    def test_cso_v1_matches_iso_evidence(self):
        cso = self.root / "game.cso"
        write_fixture_cso(cso, self.source)
        iso_report = inventory_dariusburst(self.iso)
        cso_report = inventory_dariusburst(cso)
        self.assertEqual(cso_report["dzip_count"], 3)
        self.assertEqual(cso_report["extension_counts"], iso_report["extension_counts"])
        self.assertEqual([x["packed_sha256"] for x in cso_report["records"]],
                         [x["packed_sha256"] for x in iso_report["records"]])

    def test_reject_unexpected_disc_and_corrupt_envelopes(self):
        bad = self.root / "wrong.iso"
        data = bytearray(self.source)
        sfo = fixture_sfo({"DISC_ID": "NPJH50053", "TITLE": "Wrong",
                           "DISC_VERSION": "1.01"})
        record = rec(b"PARAM.SFO;1", 23, len(sfo), False)
        original_icon = rec(b"ICON0.PNG;1", 22, len(PNG), False)
        first = 21 * SECTOR + len(original_icon)
        data[first:first + len(record)] = record
        data[23 * SECTOR:24 * SECTOR] = sfo + b"\0" * (SECTOR - len(sfo))
        bad.write_bytes(data)
        with self.assertRaisesRegex(DariusburstREError,"ULJM05558"):
            inventory_dariusburst(bad)
        damaged = self.root / "damaged.iso"
        changed = bytearray(self.source)
        changed[26 * SECTOR + 4:26 * SECTOR + 8] = b"\xff" * 4
        damaged.write_bytes(changed)
        report = inventory_dariusburst(damaged)
        self.assertEqual(report["dzip_count"], 2)
        self.assertEqual(report["records"][0]["status"], "bad_dzip_header")
        self.assertEqual(len(report["errors"]), 1)

    def test_metadata_only_file_publication_refuses_overwrite(self):
        report = inventory_dariusburst(self.iso)
        dest = self.root / "evidence" / "manifest.json"
        safe_report_write(dest, report)
        self.assertIn("dzip_envelope_only", dest.read_text(encoding="utf-8"))
        self.assertEqual(self.iso.read_bytes(), self.source)
        with self.assertRaises(DariusburstREError):
            safe_report_write(dest, report)

    @unittest.skipIf(Image is None, "Pillow opcional no instalado")
    def test_capture_diff_requires_ppsspp_hash_and_exact_pixels(self):
        folder_a = self.root / "captures-a"
        folder_b = self.root / "captures-b"
        folder_a.mkdir()
        folder_b.mkdir()
        same = "000000005566778899aabbcc.png"
        changed = "000000008899aabbccddeeff.png"
        fresh = "00000000abcdef0123456789.png"
        for folder in (folder_a,folder_b):
            Image.new("RGBA",(8,8),(100,10,200,255)).save(folder / same)
        Image.new("RGBA",(16,8),(90,120,150,255)).save(folder_a / changed)
        Image.new("RGBA",(16,8),(50,120,150,255)).save(folder_b / changed)
        Image.new("RGBA",(4,4),(250,240,20,255)).save(folder_b / fresh)
        (folder_b / "unsafe.png").write_bytes(b"fake")
        a,b=capture_inventory(folder_a),capture_inventory(folder_b)
        self.assertEqual(b["invalid"],1)
        comparison=compare_capture_sessions(a,b)
        self.assertEqual(comparison["before"],2)
        self.assertEqual(comparison["after"],3)
        self.assertEqual(comparison["repeated_same_pixels"],1)
        self.assertEqual(comparison["same_hash_different_pixels"],1)
        self.assertEqual(comparison["new_hashes"],[fresh[:-4]])
        self.assertFalse(comparison["source_mapping_verified"])
        match = correlate_decoded_candidates(b,[{
            "source_path":"IMAGES/ILLUST01.TX2",
            "pixels_sha256":b["entries"][0]["pixels_sha256"],
            "width":b["entries"][0]["width"],
            "height":b["entries"][0]["height"],
            "decoded_and_source_verified":True,
        }])
        self.assertEqual(match["published_replacements"],0)
        self.assertFalse(match["pixel_matches_unverified"][0]["publishing_authorized"])
        self.assertFalse(match["pixel_matches_unverified"][0]["source_provenance_verified"])

    def test_reject_malformed_comparison_no_false_confidence(self):
        with self.assertRaises(DariusburstREError):
            compare_capture_sessions({"entries":[{"hash":"invalid"}]},
                                     {"entries":[]})
        with self.assertRaises(DariusburstREError):
            compare_capture_sessions({"entries":[]}, {"count":0})


class DariusburstDockIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dock = Dock(self.root / "data")
        self.iso = self.root / "data" / "library" / "ISO" / "Dariusburst.iso"
        self.iso.parent.mkdir(parents=True,exist_ok=True)
        self.original = synthetic_dariusburst_iso(self.iso)
        self.game_id = "a" * 24
        self.dock.cached_game_results = [{
            "id":self.game_id,"availability":"ready",
            "local_relative":"ISO/Dariusburst.iso"}]
        self.dock.last_games = [{
            "id":self.game_id,"relative":"ISO/Dariusburst.iso",
            "title":"Dariusburst","format":"ISO","size":len(self.original)}]

    def _wait_job(self):
        for _ in range(80):
            with self.dock.hd_scan_lock:
                progress=dict(self.dock.hd_scan_state)
            if not progress["active"]:
                return progress
            time.sleep(.1)
        self.fail("La tarea de análisis no terminó.")

    def test_dock_offline_inventory_and_xmb_status(self):
        started=self.dock.start_hd_iso_scan(self.game_id)
        self.assertTrue(started["active"])
        report=self._wait_job()
        self.assertEqual(report["status"],"finished",report)
        status=self.dock.hd_generator_status(GAME_DISC_ID)
        self.assertEqual(status["darius_re"]["archive"]["dzip_files"],3)
        self.assertEqual(status["darius_re"]["archive"]["decoded_assets"],0)
        self.assertEqual(status["darius_re"]["archive"]["replacements_proven"],0)
        self.assertEqual(self.iso.read_bytes(),self.original)
        # A repeat scan does not overwrite an evidence report.
        self.dock.start_hd_iso_scan(self.game_id)
        self.assertEqual(self._wait_job()["status"],"finished")

    @unittest.skipIf(Image is None, "Pillow opcional no instalado")
    def test_two_capture_snapshots_are_compared_without_writing_to_psp(self):
        folder=Path(self.dock.config["memstick"])/"PSP/TEXTURES"/GAME_DISC_ID/"new"
        folder.mkdir(parents=True)
        name="000000005566778899aabbcc.png"
        Image.new("RGBA",(8,8),(90,100,110,255)).save(folder/name)
        self.dock.start_darius_capture_snapshot(self.game_id)
        self.assertEqual(self._wait_job()["status"],"finished")
        self.assertEqual(
            self.dock.hd_generator_status(GAME_DISC_ID)["darius_re"]["capture"]["captured"],1)
        self.dock.start_darius_capture_snapshot(self.game_id)
        self.assertEqual(self._wait_job()["status"],"finished")
        status=self.dock.hd_generator_status(GAME_DISC_ID)
        self.assertTrue(status["darius_re"]["capture"]["compared"])
        self.assertEqual(status["darius_re"]["capture"]["repeated"],1)
        self.assertFalse(status["darius_re"]["capture"]["source_mapping_verified"])
        self.assertEqual(self.iso.read_bytes(),self.original)


if __name__=="__main__":
    unittest.main()
