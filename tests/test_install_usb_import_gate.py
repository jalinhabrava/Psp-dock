import hashlib
from pathlib import Path
import tempfile
import unittest

from install_usb_import_gate import DEST, install_package


class InstallUsbImportGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/SLOT").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/SLOT/DATA.BIN").write_bytes(b"keep")
        self.sources = {}
        for name, content in (("EBOOT.PBP", b"fixture_eboot"),
                              ("usbstate.prx", b"fixture_prx")):
            p = self.root / ("source_" + name)
            p.write_bytes(content)
            self.sources[name] = (p, hashlib.sha256(content).hexdigest())

    def tearDown(self):
        self.tmp.cleanup()

    def test_stages_two_files_and_never_overwrites(self):
        result = install_package(self.root, self.sources)
        self.assertEqual(result, self.root / DEST)
        for name, (source, _) in self.sources.items():
            self.assertEqual((result / name).read_bytes(), source.read_bytes())
        with self.assertRaisesRegex(ValueError, "No se sobrescribe"):
            install_package(self.root, self.sources)
        self.assertEqual((self.root / "PSP/SAVEDATA/SLOT/DATA.BIN").read_bytes(), b"keep")

    def test_wrong_hash_refuses_write(self):
        sources = {**self.sources}
        p, _ = sources["usbstate.prx"]
        sources["usbstate.prx"] = (p, "0" * 64)
        with self.assertRaisesRegex(ValueError, "checksum"):
            install_package(self.root, sources)
        self.assertFalse((self.root / DEST).exists())
        self.assertFalse(list((self.root / "PSP/GAME").glob(".dock-gate-stage-*")))

    def test_requires_both_allowlisted_components(self):
        for bad in (
            {"EBOOT.PBP": self.sources["EBOOT.PBP"]},
            {**self.sources, "README.txt": self.sources["EBOOT.PBP"]},
        ):
            with self.assertRaises(ValueError):
                install_package(self.root, bad)
        self.assertFalse((self.root / DEST).exists())


if __name__ == "__main__":
    unittest.main()
