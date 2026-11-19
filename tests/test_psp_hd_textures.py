"""PC-only hybrid texture tests: PPSSPP integration, ISO inventory and safety."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:
    Image = None

import psp_dock.emulation.ppsspp_graphics as gfx
import psp_dock.emulation.psp_hd_textures as hd
from dock import Dock
from tests.test_psp_artwork import rec, write_fixture_cso, write_fixture_iso, PNG
from tests.test_psp_game_metadata import fixture_sfo
from psp_dock.media.psp_artwork import SECTOR


DISC = "NPJH50053"
HASH = "00000000aabbccddeeff0099"


class HybridPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memstick = self.root / "PC-memory-stick"
        self.memstick.mkdir()

    def test_new_pack_uses_ppsspp_hashes_and_refuses_existing_external_pack(self):
        with self.assertRaises(hd.TextureGenerationError):
            hd.prepare_pack(self.memstick, "../OUTSIDE")
        folder = hd.prepare_pack(self.memstick, DISC)
        self.assertEqual(folder, hd.prepare_pack(self.memstick, DISC))
        ini = (folder / "textures.ini").read_text()
        self.assertIn("hash = xxh64", ini)
        self.assertIn("ignoreAddress = true", ini)
        self.assertTrue(hd.generator_owned(folder))
        self.assertEqual(hd.installed_status(self.memstick, DISC)["kind"], "generated")
        (folder / "textures.zip").write_bytes(b"foreign pack")
        with self.assertRaises(hd.TextureGenerationError):
            hd.prepare_pack(self.memstick, DISC)
        (folder / "textures.zip").unlink()
        (folder / "textures.ini").write_text("[options]\nversion=1\n")
        with self.assertRaises(hd.TextureGenerationError):
            hd.prepare_pack(self.memstick, DISC)

    def test_capture_is_progressively_scaled_and_never_overwritten(self):
        if Image is None:
            self.skipTest("Pillow opcional no instalado")
        folder = hd.prepare_pack(self.memstick, DISC)
        captured = folder / "new"
        captured.mkdir()
        source = captured / (HASH + ".png")
        Image.new("RGBA", (14, 12), (30, 100, 210, 110)).save(source)
        self.assertEqual(
            hd.generate_one(self.memstick, DISC, source, hd.TextureSettings(scale=4),
                            stable_seconds=0),
            "generated")
        result = folder / source.name
        with Image.open(result) as image:
            self.assertEqual(image.size, (56, 48))
        first = result.read_bytes()
        # When a game repeats or changes the dump, neither it nor its
        # replacement is modified by another generation pass.
        Image.new("RGB", (16, 8), (30, 20, 10)).save(source)
        self.assertEqual(
            hd.generate_one(self.memstick, DISC, source, hd.TextureSettings(scale=2),
                            stable_seconds=0),
            "skipped")
        self.assertEqual(result.read_bytes(), first)
        self.assertEqual(hd.installed_status(self.memstick, DISC)["generated"], 1)

    def test_batch_skips_completed_textures_and_detects_invalid_names(self):
        if Image is None:
            self.skipTest("Pillow opcional no instalado")
        folder = hd.prepare_pack(self.memstick, DISC)
        raw = folder / "new"
        raw.mkdir()
        for suffix in ("11", "22", "33"):
            Image.new("RGB", (9, 7)).save(raw / (suffix * 12 + ".png"))
        (raw / "somefile.png").write_bytes(b"not a hash")
        for _ in range(2):
            result = hd.process_dump_batch(self.memstick, DISC, hd.TextureSettings(),
                                           limit=2, stable_seconds=0)
            self.assertEqual(result["errors"], 0)
        self.assertEqual(hd.installed_status(self.memstick, DISC)["generated"], 3)
        # Avoid arbitrary filesystem traversal and rejected path names.
        with self.assertRaises(hd.TextureGenerationError):
            hd.generate_one(self.memstick, DISC, raw / ".." / "other.png",
                            hd.TextureSettings(), stable_seconds=0)

    def test_only_stable_images_are_processed(self):
        if Image is None:
            self.skipTest("Pillow opcional no instalado")
        folder = hd.prepare_pack(self.memstick, DISC)
        raw = folder / "new"
        raw.mkdir()
        source = raw / (HASH + ".png")
        Image.new("RGB", (8, 8)).save(source)
        self.assertEqual(hd.generate_one(self.memstick, DISC, source,
                                         hd.TextureSettings(), stable_seconds=30),
                         "deferred")
        self.assertFalse((folder / source.name).exists())

    def test_worker_manual_drain_stops_automatically(self):
        if Image is None:
            self.skipTest("Pillow opcional no instalado")
        folder = hd.prepare_pack(self.memstick, DISC)
        raw = folder / "new"
        raw.mkdir()
        Image.new("RGBA", (12, 12)).save(raw / (HASH + ".png"))
        worker = hd.TextureWorker(self.memstick)
        worker.start(DISC, hd.TextureSettings(), until_idle=True)
        for _ in range(45):
            if (folder / (HASH + ".png")).is_file() and not worker.status()["active"]:
                break
            time.sleep(.12)
        worker.stop()
        self.assertTrue((folder / (HASH + ".png")).is_file())
        self.assertFalse(worker.status()["active"])
        self.assertEqual(worker.status()["generated"], 1)

    def test_readonly_iso_and_cso_inventory_cannot_claim_ppsspp_hashes(self):
        iso = self.root / "owned.iso"
        write_fixture_iso(iso)
        before = iso.read_bytes()
        report = hd.discover_iso_assets(iso, self.root / "cache",
                                        disc_id=DISC)
        self.assertEqual(before, iso.read_bytes())
        self.assertTrue(report["unmapped"])
        self.assertGreater(len(report["candidates"]), 0)
        self.assertFalse(any(c["mapped"] for c in report["candidates"]))
        self.assertEqual((self.root / "cache" / DISC / "index.json").is_file(), True)
        with self.assertRaises(hd.TextureGenerationError):
            hd.discover_iso_assets(iso, self.root / "cache", disc_id=DISC)

        cso = self.root / "owned.cso"
        write_fixture_cso(cso, before)
        report = hd.discover_iso_assets(cso, self.root / "cso-cache",
                                        disc_id=DISC)
        self.assertTrue(report["unmapped"])
        self.assertTrue(any(c["format"] == "png" for c in report["candidates"]))

    def test_bad_iso_and_oversized_input_do_not_create_replacements(self):
        bad = self.root / "broken.iso"
        bad.write_bytes(b"\0" * (18 * SECTOR))
        with self.assertRaises(hd.TextureGenerationError):
            hd.discover_iso_assets(bad, self.root / "bad-cache", disc_id=DISC)
        folder = hd.prepare_pack(self.memstick, DISC)
        raw = folder / "new"
        raw.mkdir()
        source = raw / (HASH + ".png")
        source.write_bytes(b"\x00" * (hd.MAX_INPUT_BYTES + 1))
        self.assertEqual(hd.generate_one(self.memstick, DISC, source,
                                         hd.TextureSettings(), stable_seconds=0),
                         "skipped")
        self.assertFalse((folder / source.name).exists())


class DockHDIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        psp = self.root / "synthetic-psp"
        (psp / "PSP/SAVEDATA").mkdir(parents=True)
        iso = psp / "ISO/sample.iso"
        write_fixture_iso(iso)
        data = bytearray(iso.read_bytes())
        sfo = fixture_sfo()
        icon_rec = rec(b"ICON0.PNG;1", 22, len(PNG), False)
        param_rec = rec(b"PARAM.SFO;1", 23, len(sfo), False)
        data[21 * SECTOR + len(icon_rec):21 * SECTOR +
             len(icon_rec) + len(param_rec)] = param_rec
        data[23 * SECTOR:23 * SECTOR + len(sfo)] = sfo
        iso.write_bytes(data)
        self.dock = Dock(self.root / "data")
        self.dock.settings({"psp_root": str(psp)})
        self.game_id = self.dock.games()[0]["id"]
        self.dock.transfer([self.game_id])

    def test_enable_generator_only_after_local_verification(self):
        view = self.dock.graphics_view(self.game_id)
        self.assertEqual(view["hd_generator"]["kind"], "none")
        self.assertFalse(view["hd_generator"]["enabled"])
        with self.assertRaises(ValueError):
            self.dock.configure_hd_generator("a" * 24, action="enable")
        report = self.dock.configure_hd_generator(
            self.game_id, action="enable", scale=2)
        self.assertTrue(report["enabled"])
        self.assertEqual(report["kind"], "generated")
        mem = Path(self.dock.config["memstick"])
        self.assertTrue((mem / "PSP/TEXTURES" / DISC / "textures.ini").is_file())
        self.assertEqual(self.dock.hd_jobs["games"][DISC]["scale"], 2)
        self.dock.configure_hd_generator(self.game_id, action="disable")
        self.assertFalse(self.dock.graphics_view(self.game_id)["hd_generator"]["enabled"])

    def test_private_ini_capture_is_per_game_opt_in_and_reversible(self):
        self.dock.configure_hd_generator(self.game_id, action="enable", scale=4)
        private = Path(self.dock.config["memstick"]) / "PSP/SYSTEM/ppsspp.ini"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("[Graphics]\nInternalResolution = 3\n")
        self.dock.apply_game_graphics(self.game_id)
        self.assertIn("SaveNewTextures = True", private.read_text())
        self.assertIn("ReplaceTextures = True", private.read_text())
        self.dock.configure_hd_generator(self.game_id, action="disable")
        self.dock.apply_game_graphics(self.game_id)
        self.assertIn("SaveNewTextures = False", private.read_text())
        self.assertIn("ReplaceTextures = True", private.read_text())
        # A different game profile without a pack resets BOTH switches.
        default = gfx.recommend(gfx.Monitor(1920, 1080))
        gfx.apply_private_ppsspp_ini(private, plan={
            **default, "replace_textures": False, "capture_textures": False,
        }, backup_dir=self.root / "private-backups")
        self.assertIn("SaveNewTextures = False", private.read_text())
        self.assertIn("ReplaceTextures = False", private.read_text())

    def test_gpu_shader_uses_ppsspp_native_vulkan_without_cpu_double_scale(self):
        plan = gfx.recommend(gfx.Monitor(1920, 1080),
                             texture="gpu4", backend="vulkan")
        self.assertTrue(plan["texture_gpu_active"])
        self.assertFalse(plan["texture_upscale_on_cpu"])
        private = self.root / "ppsspp.ini"
        private.write_text("[Graphics]\n")
        gfx.apply_private_ppsspp_ini(private, plan=plan,
                                     backup_dir=self.root / "backups")
        self.assertIn("TextureShader = Tex4xBRZ", private.read_text())
        self.assertIn("TexScalingLevel = 1", private.read_text())
        fallback = gfx.recommend(gfx.Monitor(1920,1080),
                                 texture="gpu4",backend="directx11")
        self.assertFalse(fallback["texture_gpu_active"])
        gfx.apply_private_ppsspp_ini(private, plan=fallback,
                                     backup_dir=self.root / "backups")
        self.assertIn("TextureShader = Off", private.read_text())


if __name__ == "__main__":
    unittest.main()
