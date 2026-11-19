"""Linux desktop/recovery checks use only temporary files and mocked launchers."""
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import Mock, patch

from psp_dock.core import linux_desktop as desktop


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class DataAndWindowTests(unittest.TestCase):
    def test_xdg_location_is_private_and_demo_is_separate(self):
        env = {'XDG_DATA_HOME': '/home/player/custom-data'}
        self.assertEqual(desktop.default_data_dir(environ=env), Path('/home/player/custom-data/psp-dock/live'))
        self.assertEqual(desktop.default_data_dir(environ=env, demo=True), Path('/home/player/custom-data/psp-dock/demo'))

    def test_empty_and_relative_xdg_values_use_home_fallback(self):
        for value in ('', 'relative', '~/data'):
            self.assertEqual(desktop.default_data_dir(environ={'XDG_DATA_HOME':value}, home='/home/p'),
                             Path('/home/p/.local/share/psp-dock/live'))

    def test_browser_receives_fixed_local_url_as_one_argument(self):
        launch = Mock(return_value='process')
        result = desktop.launch_xmb_window('http://127.0.0.1:59336', app_window=True,
            which=lambda name: '/usr/bin/chromium' if name == 'chromium' else None,
            popen=launch, open_url=Mock(side_effect=AssertionError('Unexpected tab')))
        self.assertEqual(result, 'process')
        self.assertEqual(launch.call_args.args[0], ['/usr/bin/chromium', '--new-window',
                         '--app=http://127.0.0.1:59336', '--start-maximized'])
        self.assertNotIn('shell', launch.call_args.kwargs)

    def test_missing_chromium_falls_back_to_default_browser(self):
        opener = Mock(return_value=True)
        desktop.launch_xmb_window('http://127.0.0.1:59336', app_window=True,
                                  which=lambda _:None, open_url=opener)
        opener.assert_called_once_with('http://127.0.0.1:59336')

    def test_non_local_urls_and_invalid_ports_never_open(self):
        for url in ('https://example.org', 'http://127.0.0.1:80', 'http://127.0.0.1:99999',
                    'http://127.0.0.1:59336/ --bad', 'http://127.0.0.1:59336@evil.example'):
            with self.subTest(url=url), self.assertRaises(desktop.LinuxDesktopError):
                desktop.launch_xmb_window(url, open_url=Mock(side_effect=AssertionError('Opened')))


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class ProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def process(self, pid, name, *, exe=None, state='S'):
        directory = self.root/str(pid)
        directory.mkdir()
        (directory/'status').write_text('Name:\t'+name+'\nState:\t'+state+' (test)\n')
        (directory/'comm').write_text(name+'\n')
        (directory/'cmdline').write_bytes((exe or '/usr/bin/'+name).encode()+b'\0game.iso\0')
        try:
            (directory/'exe').symlink_to(exe or '/usr/bin/'+name)
        except (OSError, NotImplementedError):
            self.skipTest('Synthetic /proc needs symlinks')
        return directory

    def test_recovery_rejects_both_emulators_and_never_kills_them(self):
        self.process(21,'PPSSPPSDL')
        self.process(22,'retroarch')
        self.assertEqual([p['pid'] for p in desktop.running_emulators(proc_root=self.root)], [21,22])
        with self.assertRaisesRegex(desktop.LinuxDesktopError,'Cierra el emulador'):
            desktop.assert_emulators_stopped(proc_root=self.root)
        self.assertTrue((self.root/'21').is_dir())

    def test_unrelated_processes_and_zombies_do_not_block(self):
        self.process(21,'python')
        self.process(22,'PPSSPPSDL',state='Z')
        desktop.assert_emulators_stopped(proc_root=self.root)

    def test_exact_configured_executable_detects_renamed_emulator(self):
        self.process(21,'custom',exe='/opt/emulators/custom')
        with self.assertRaises(desktop.LinuxDesktopError):
            desktop.assert_emulators_stopped(proc_root=self.root,executables=['/opt/emulators/custom'])

    def test_an_unreadable_process_cannot_be_treated_as_closed(self):
        directory = self.process(21,'PPSSPPSDL')
        (directory/'cmdline').unlink()
        with self.assertRaises(desktop.LinuxDesktopError):
            desktop.assert_emulators_stopped(proc_root=self.root)

    def test_processes_of_other_users_do_not_read_private_data(self):
        (self.root/'21').mkdir()
        self.assertEqual(desktop.running_emulators(proc_root=self.root,uid=os.getuid()+1), [])


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class ManualLauncherTests(unittest.TestCase):
    def test_explicit_linux_path_requires_existing_dock_and_rejects_symlink(self):
        import start_dock
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'PSP_DOCK_TEST_NO_REAL_USB': '1'}):
            root = Path(temporary)
            unrelated = root / 'unrelated'
            unrelated.mkdir()
            (unrelated / 'important.txt').write_text('untouched')
            with patch.object(start_dock, '_checked_server') as server:
                with self.assertRaises(ValueError):
                    start_dock.launch(unrelated)
                server.assert_not_called()
            self.assertEqual(sorted(p.name for p in unrelated.iterdir()), ['important.txt'])
            live = root / 'live'
            live.mkdir()
            (live / 'session.json').write_text('{}')
            (live / 'library').mkdir()
            link = root / 'link'
            link.symlink_to(live, target_is_directory=True)
            with patch.object(start_dock, '_checked_server') as server:
                with self.assertRaises(ValueError):
                    start_dock.launch(link)
                server.assert_not_called()

    def test_connected_linux_ui_does_not_open_another_window(self):
        import start_dock
        health={'ui_lifecycle':True,'ui_clients':1,'ui_connected_once':True}
        with patch('start_dock.sys.platform','linux'), \
                patch.object(start_dock,'_checked_server',return_value={'url':'http://127.0.0.1:59336','health':health}), \
                patch.object(start_dock,'launch_xmb_window') as browser, \
                patch.object(start_dock,'serve') as serve:
            self.assertEqual(start_dock.launch(),'already_running')
            browser.assert_not_called()
            serve.assert_not_called()

    def test_disconnected_linux_ui_reopens_same_verified_service(self):
        import start_dock
        health={'ui_lifecycle':True,'ui_clients':0,'ui_connected_once':True,'native_handles':[123]}
        with patch('start_dock.sys.platform','linux'), \
                patch.object(start_dock,'_checked_server',return_value={'url':'http://127.0.0.1:59336','health':health}), \
                patch.object(start_dock,'launch_xmb_window') as browser, \
                patch.object(start_dock,'serve') as serve:
            self.assertEqual(start_dock.launch(),'already_running')
            browser.assert_called_once_with('http://127.0.0.1:59336',app_window=True,
                                            reuse_existing=False,include_handles=set())
            serve.assert_not_called()

    def test_first_linux_launch_uses_xdg_and_has_no_autostart_side_effect(self):
        import start_dock
        with patch('start_dock.sys.platform','linux'), \
                patch.dict(os.environ,{'XDG_DATA_HOME':'/tmp/psp-dock-synthetic-xdg'}), \
                patch.object(start_dock,'_checked_server',return_value=None), \
                patch.object(start_dock,'Dock') as dock, patch.object(start_dock,'serve') as serve:
            self.assertEqual(start_dock.launch(),'started')
            dock.assert_called_once_with(Path('/tmp/psp-dock-synthetic-xdg/psp-dock/live'))
            self.assertEqual(serve.call_args.kwargs,{'ui_lifecycle':True})


if __name__ == '__main__':
    unittest.main()
