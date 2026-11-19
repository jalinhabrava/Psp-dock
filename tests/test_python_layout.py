"""Structural guard for stable public entrypoints and the organized host tree."""

from pathlib import Path
import ast
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PythonLayoutTests(unittest.TestCase):
    def test_only_stable_root_python_entrypoints(self):
        root_modules = {path.name for path in ROOT.glob('*.py')}
        self.assertEqual(root_modules, {'dock.py', 'start_dock.py'})

    def test_package_directories_exist(self):
        for relative in ('psp_dock/core', 'psp_dock/devices',
                         'psp_dock/emulation', 'psp_dock/media',
                         'tools/psp'):
            with self.subTest(package=relative):
                self.assertTrue((ROOT / relative / '__init__.py').is_file())

    def test_only_final_psp_homebrew_is_shipped(self):
        self.assertEqual({directory.name for directory in (ROOT / 'homebrew').iterdir()
                          if directory.is_dir() and not directory.name.startswith('__')},
                         {'native-dock'})
        self.assertTrue((ROOT / 'homebrew/native-dock/lab_gate.h').is_file())
        self.assertTrue((ROOT / 'homebrew/native-dock/elf_reader.py').is_file())
        self.assertTrue((ROOT / 'homebrew/native-dock/LICENSE-FuSa-GPL-3.0.txt').is_file())

    def test_source_files_parse_and_use_no_runtime_sys_path_hacks(self):
        # Legacy developer helpers directly under tools/ still support
        # invocation by file path; package modules use absolute imports.
        for directory in (ROOT / 'psp_dock', ROOT / 'tools/labs',
                          ROOT / 'tools/psp', ROOT / 'tools/system'):
            for source in directory.rglob('*.py'):
                with self.subTest(source=source.relative_to(ROOT)):
                    tree = ast.parse(source.read_text(encoding='utf-8'))
                    for node in ast.walk(tree):
                        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                            continue
                        target = node.func.value
                        self.assertFalse(
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == 'sys'
                            and target.attr == 'path'
                            and node.func.attr in ('insert', 'append', 'extend'),
                            f'{source.relative_to(ROOT)} changes sys.path at runtime',
                        )


if __name__ == '__main__':
    unittest.main()
