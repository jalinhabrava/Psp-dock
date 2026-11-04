from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from psp_dock.core.catalog_paths import ReadOnlyTree
from psp_dock.emulation.retro_runtime import snapshot_private_saves


class CatalogPathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_thousand_missing_paths_enumerate_missing_parent_once(self):
        tree=ReadOnlyTree(self.root)
        for i in range(1500):
            self.assertIsNone(tree.file('PSP/GAME/Emulator/ROM/'+str(i)+'.gba')[1])
        self.assertEqual(tree.scans,1)

    def test_new_scope_detects_add_remove_and_updated_size(self):
        (self.root/'ROM').mkdir();file=self.root/'ROM/a.gba';file.write_bytes(b'one')
        self.assertEqual(ReadOnlyTree(self.root).file('ROM/a.gba')[1].st_size,3)
        file.write_bytes(b'changed')
        self.assertEqual(ReadOnlyTree(self.root).file('ROM/a.gba')[1].st_size,7)
        file.unlink();self.assertIsNone(ReadOnlyTree(self.root).file('ROM/a.gba')[1])

    def test_traversal_and_absolute_paths_never_query_a_directory(self):
        tree=ReadOnlyTree(self.root)
        for rel in ('../outside','ROM/../a','ROM/../../a','/outside','C:/outside','ROM\\..\\a','a:b',''):
            with self.subTest(path=rel),self.assertRaises(ValueError):tree.file(rel)
        self.assertEqual(tree.scans,0)

    def test_symlink_is_rejected_even_when_it_points_inside_root(self):
        (self.root/'real').mkdir()
        try:(self.root/'linked').symlink_to(self.root/'real',target_is_directory=True)
        except OSError:self.skipTest('Host does not allow symlink creation')
        with self.assertRaises(ValueError):ReadOnlyTree(self.root).file('linked/title.gba')

    def test_missing_sram_does_not_resolve_every_absent_save_or_state_path(self):
        ids=[f'{i:024x}' for i in range(410)]
        with patch('psp_dock.emulation.retro_runtime.private_save_paths',side_effect=AssertionError('absent path revalidated')):
            self.assertEqual(snapshot_private_saves(self.root,'123',ids),dict.fromkeys(ids))

    def test_present_sram_retains_fresh_path_guard_and_hash(self):
        from psp_dock.emulation.retro_runtime import private_save_paths
        from psp_dock.emulation.retro_saves import _sha
        key='a'*24;file,_=private_save_paths(self.root,'123',key)
        file.parent.mkdir(parents=True);file.write_bytes(b'a'*32768)
        with patch('psp_dock.emulation.retro_runtime.private_save_paths',wraps=private_save_paths) as guard:
            self.assertEqual(snapshot_private_saves(self.root,'123',[key]),{key:_sha(file)})
            guard.assert_called_once()
