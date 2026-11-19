"""Windows login helper tests. All writes go to private temp fixtures."""
from pathlib import Path
import tempfile
import unittest

import psp_dock.core.setup_startup as setup


class StartupInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.start = setup.startup_file(self.home / 'Roaming')

    def tearDown(self):
        self.temp.cleanup()

    def live(self, name='PSP-Dock/live'):
        path = self.home / 'Local' / name
        path.mkdir(parents=True, exist_ok=True)
        (path/'settings.json').write_text('{}')
        (path/'session.json').write_text('{}')
        return path

    def test_installer_writes_only_marker_entry_and_does_not_run_it(self):
        payload = setup.render_startup_command(
            self.home/'Python/pythonw.exe', self.home/'project/dock.py',
            self.live(), 8765,
        )
        self.assertIn('--no-browser --open-on-connect --app-window --port 8765', payload)
        self.assertIn(setup.MARKER, payload)
        self.assertFalse(self.start.exists())
        self.assertTrue(setup.install_entry(self.start, payload))
        self.assertFalse(setup.install_entry(self.start, payload))
        self.assertEqual(self.start.read_text(encoding='utf-8'),
                         payload.replace('\r\n','\n'))
        self.assertTrue(setup.remove_entry(self.start))
        self.assertFalse(setup.remove_entry(self.start))

    def test_ambiguous_live_locations_require_explicit_data(self):
        first = self.live()
        self.assertEqual(setup.choose_live_dir(self.home/'Local'), first.resolve())
        extra = self.live('Packages/OpenAI.Codex_fixture/LocalCache/Local/PSP-Dock/live')
        with self.assertRaisesRegex(ValueError, 'inequívoca'):
            setup.choose_live_dir(self.home/'Local')
        self.assertEqual(setup.choose_live_dir(self.home/'Local', extra), extra.resolve())

    def test_no_silent_blank_library_and_no_foreign_startup_overwrite(self):
        with self.assertRaisesRegex(ValueError, 'inequívoca'):
            setup.choose_live_dir(self.home/'Local')
        self.start.parent.mkdir(parents=True)
        self.start.write_text('@echo off\nREM unrelated owner')
        with self.assertRaisesRegex(ValueError, 'otro inicio'):
            setup.install_entry(self.start, setup.MARKER)
        with self.assertRaisesRegex(ValueError, 'no se eliminará'):
            setup.remove_entry(self.start)
        self.assertTrue(self.start.exists())

    def test_bad_ports_and_shell_metacharacters_are_rejected(self):
        for bad in (80, 65536, -1):
            with self.subTest(port=bad), self.assertRaises(ValueError):
                setup.render_startup_command(self.home/'pythonw.exe',
                                             self.home/'dock.py', self.home/'live', bad)
        for bad in ('name%EXPAND', 'new!VAR', 'bad"quote', 'caret^path', 'multi\nline'):
            with self.subTest(path=bad), self.assertRaisesRegex(ValueError, 'incompatibles'):
                setup.render_startup_command(self.home/'pythonw.exe',
                                             self.home/bad/'dock.py', self.home/'live', 8765)

    def test_refuses_symlink_at_startup_file(self):
        target = self.home/'outside.txt'
        target.write_text('foreign')
        self.start.parent.mkdir(parents=True)
        try:
            self.start.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation not permitted under current Windows account')
        with self.assertRaises(ValueError):
            setup.install_entry(self.start, setup.MARKER)
        with self.assertRaises(ValueError):
            setup.remove_entry(self.start)
        self.assertEqual(target.read_text(), 'foreign')


if __name__ == '__main__':
    unittest.main()
