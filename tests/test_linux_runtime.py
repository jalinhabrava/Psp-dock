import configparser
import json
import os
from pathlib import Path
import platform
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import dock
import linux_runtime as runtime


def executable(path):
    header = bytearray(64)
    header[:6] = b'\x7fELF\x02\x01'
    header[18:20] = {'x86_64': 62, 'aarch64': 183}[platform.machine().lower()].to_bytes(2, 'little')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header)
    path.chmod(0o755)
    return path


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.data = self.home / 'dock'
        self.data.mkdir()
        self.exe = executable(self.home / 'native/PPSSPPSDL')
        self.memstick = self.data / 'private-memstick'
        self.config = {'emulator': str(self.exe), 'memstick': str(self.memstick), 'scale': 2, 'backend': 'gles'}

    def test_prepare_and_launch_use_private_xdg_without_changing_home_or_global_profile(self):
        global_config = self.home / 'unrelated-global.ini'
        global_config.write_text('unchanged')
        original_env = dict(os.environ)
        with patch('subprocess.Popen', side_effect=AssertionError('must not run emulator while preparing')), patch('subprocess.run', side_effect=AssertionError('unsupported --version probe')):
            self.assertEqual(runtime.prepare(self.data, self.config), self.exe)
        self.assertEqual(runtime.prepared_runtime(self.data, self.config, verify_digest=True), self.exe)
        env = {'HOME': str(self.home), 'XDG_CONFIG_HOME': '/untouched/config', 'SDL_GAMECONTROLLERCONFIG': 'fake'}
        profile = lambda parent: (dict(env), b'[ControlMapping]\nCross = 10-189\n', SimpleNamespace(index=0))
        game = self.data / 'game.iso'
        args, child = runtime.ppsspp_launch(self.data, self.config, '/synthetic/usb', game, profile=profile)
        self.assertEqual(args, [str(self.exe), '--fullscreen', '--pause-menu-exit', str(game)])
        self.assertEqual(child['HOME'], env['HOME'])
        self.assertEqual(child['XDG_CONFIG_HOME'], str(self.data / 'linux-xdg'))
        self.assertEqual((Path(child['XDG_CONFIG_HOME']) / 'ppsspp').resolve(), self.memstick)
        self.assertIn('10-189', (self.memstick / 'PSP/SYSTEM/controls.ini').read_text())
        self.assertEqual(global_config.read_text(), 'unchanged')
        self.assertEqual(dict(os.environ), original_env)
        self.assertEqual(env['XDG_CONFIG_HOME'], '/untouched/config')

    def test_wrong_binary_and_windows_renderer_are_rejected(self):
        for name, payload in [('PPSSPPWindows64.exe', b'MZ'), ('PPSSPPQt', b'\x7fELF'), ('PPSSPPSDL', b'#!/bin/sh\nexit 0')]:
            path = self.home / name
            path.write_bytes(payload)
            path.chmod(0o755)
            self.assertIsNone(runtime.emulator_spec({**self.config, 'emulator': str(path)}))
        self.assertIsNone(runtime.emulator_spec({**self.config, 'backend': 'directx11'}))

    def test_existing_private_controls_are_backed_up_before_sdl_mapping(self):
        runtime.prepare(self.data, self.config)
        controls = self.memstick / 'PSP/SYSTEM/controls.ini'
        controls.write_bytes(b'[ControlMapping]\nCross = user-calibration\n')
        original = controls.read_bytes()
        runtime.ppsspp_launch(self.data, self.config, '/usb', self.data / 'game.iso',
            profile=lambda _: ({}, b'[ControlMapping]\nCross = 10-189\n', None))
        backups = list((self.data / 'profile-backups').glob('linux-controls-*.ini'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_explicit_memstick_change_invalidates_only_known_xdg_pointer(self):
        runtime.prepare(self.data, self.config)
        save = self.memstick / 'PSP/SAVEDATA/example/DATA.BIN'
        save.parent.mkdir(parents=True)
        save.write_bytes(b'unchanged save')
        runtime.forget_xdg(self.data, self.memstick)
        changed = {**self.config, 'memstick': str(self.data / 'new-memstick')}
        runtime.prepare(self.data, changed)
        self.assertEqual(save.read_bytes(), b'unchanged save')
        self.assertEqual((self.data / 'linux-xdg/ppsspp').resolve(), Path(changed['memstick']))
        with self.assertRaises(ValueError):
            runtime.forget_xdg(self.data, self.memstick)

    def test_prepared_runtime_rejects_binary_changed_without_size_or_mtime_change(self):
        runtime.prepare(self.data, self.config)
        info = self.exe.stat()
        payload = self.exe.read_bytes()
        self.exe.write_bytes(payload[:-1] + b'X')
        os.utime(self.exe, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertIsNone(runtime.prepared_runtime(self.data, self.config, verify_digest=True))

    def test_xdg_substitution_and_symlinked_controls_fail_closed(self):
        runtime.prepare(self.data, self.config)
        link = self.data / 'linux-xdg/ppsspp'
        link.unlink()
        foreign = self.home / 'foreign'
        foreign.mkdir()
        link.symlink_to(foreign, target_is_directory=True)
        self.assertIsNone(runtime.prepared_runtime(self.data, self.config))
        with self.assertRaises(ValueError):
            runtime.prepare(self.data, self.config)
        self.assertEqual(link.resolve(), foreign)
        link.unlink()
        runtime.prepare(self.data, self.config)
        controls = self.memstick / 'PSP/SYSTEM/controls.ini'
        global_profile = self.home / 'global-controls.ini'
        global_profile.write_text('do not edit')
        controls.symlink_to(global_profile)
        with self.assertRaises(ValueError):
            runtime.ppsspp_launch(self.data, self.config, '/usb', self.data / 'game.iso',
                profile=lambda _: ({}, b'new', None))
        self.assertEqual(global_profile.read_text(), 'do not edit')

    def test_retroarch_native_core_and_sdl_buttons_use_private_base_config(self):
        exe = executable(self.home / 'retro/retroarch')
        core = executable(self.home / 'cores/mesen_libretro.so')
        self.assertEqual(runtime.retroarch_core(str(exe), 'nes', str(core.parent)), (exe, core))
        profile = lambda _: ({'HOME': str(self.home)}, b'', SimpleNamespace(index=3))
        config, env = runtime.retro_profile(self.data, '/usb', profile=profile)
        text = config.read_text()
        self.assertIn('input_player1_joypad_index = "3"', text)
        self.assertIn('input_player1_start_btn = "6"', text)
        self.assertIn('input_player1_l_btn = "9"', text)
        self.assertIn('input_player1_up_btn = "11"', text)
        self.assertIn('config_save_on_exit = "false"', text)
        self.assertNotIn('dinput', text)
        args = runtime.retro_arguments(exe, core, '/rom', config, '/save', '/state')
        self.assertIn('--config', args)
        self.assertNotIn('--appendconfig', args)
        self.assertEqual(env['XDG_CONFIG_HOME'], str(self.data / 'retro-linux-xdg'))
        header = bytearray(core.read_bytes())
        header[4] = 1  # A 32-bit core must not be offered to the 64-bit runtime.
        core.write_bytes(header)
        self.assertIsNone(runtime.retroarch_core(str(exe), 'nes', str(core.parent)))


class DockLinuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        guard = patch.dict(os.environ, {'PSP_DOCK_TEST_NO_REAL_USB': '1'})
        guard.start()
        self.addCleanup(guard.stop)
        self.dock = dock.Dock(Path(self.temp.name) / 'dock')
        self.before = {'path': '/dev/fake', 'session_id': '12' * 16, 'volume_serial': '123', 'usb_parent': '/sys/fake'}
        self.dock.native_transport = MagicMock(spec=['assert_controller', 'current'])
        self.dock.native_transport.assert_controller.return_value = dict(self.before)
        self.dock.product_flow = MagicMock(enabled=True)

    def test_constructor_and_platform_defaults_do_not_probe_usb(self):
        self.assertEqual(self.dock.config['backend'], 'gles')
        self.assertEqual(self.dock.config['retro_install_policy'], 'off')
        self.assertEqual(self.dock.devices(), [])
        self.assertFalse(self.dock.start_retro_install(automatic=True))
        with self.assertRaises(ValueError):
            self.dock.start_retro_install()

    def test_real_linux_writes_cannot_fall_back_to_legacy_sysfs_identity_only(self):
        with patch.dict(os.environ, {'PSP_DOCK_TEST_NO_REAL_USB': '0'}), \
                patch('linux_native.LinuxSystem.identify', side_effect=AssertionError('must require PDK2 first')):
            with self.assertRaisesRegex(ValueError, 'fase de almacenamiento'):
                dock.require_psp_identity_before_writing('/synthetic/psp')

    def test_changed_session_prevents_child_and_session_write(self):
        self.dock.native_transport.assert_controller.return_value = {**self.before, 'volume_serial': '999'}
        with patch('subprocess.Popen') as spawn, self.assertRaises(ValueError):
            self.dock._spawn_linux(['/fake'], {}, 'game', 'ppsspp', self.before)
        spawn.assert_not_called()
        self.assertFalse(self.dock.session['running'])

    def test_game_exit_returns_to_menu_without_syncing_back(self):
        child = MagicMock()
        child.poll.return_value = None
        with patch('subprocess.Popen', return_value=child) as spawn:
            self.dock._spawn_linux(['/fake', '/game'], {'HOME': '/home/fake'}, 'game', 'ppsspp', self.before)
        spawn.assert_called_once_with(['/fake', '/game'], cwd=self.dock.data, env={'HOME': '/home/fake'})
        record = json.loads((self.dock.data / 'session.json').read_text())
        self.assertTrue(record['running'])
        self.assertTrue(record['pending_return'])
        child.poll.return_value = 0
        with patch.object(self.dock, 'sync') as sync, patch.object(self.dock, 'request_exit') as exit_:
            self.assertFalse(self.dock.running())
        sync.assert_not_called()
        exit_.assert_not_called()
        self.assertTrue(self.dock.session['pending_return'])
        self.assertNotIn('return_sync_authorized', self.dock.session)
        self.dock.product_flow.set_game_running.assert_called_with(False)

    def test_spawn_failure_releases_running_phase_and_keeps_return_pending(self):
        with patch('subprocess.Popen', side_effect=OSError('not executable')), self.assertRaises(OSError):
            self.dock._spawn_linux(['/fake'], {}, 'game', 'ppsspp', self.before)
        self.assertFalse(self.dock.session['running'])
        self.dock.product_flow.set_game_running.assert_called_with(False)

    def test_linux_input_dispatch_rejects_retired_revision_before_reader(self):
        self.dock.snapshot['controller_status'] = {'connected': True, 'mode': 'native_dock',
            'product_id': 0x01cd, 'revision': 0x0100, 'protocol_version': 3,
            'firmware_revision': 0x0302, 'host_confirmed': True}
        self.dock.product_flow.snapshot.return_value = {'phase': 'ready'}
        with patch('linux_input.PSPLinuxReader') as reader:
            reader.return_value.read.return_value = {'connected': True}
            self.assertTrue(self.dock.psp_launcher_input()['connected'])
            for bad in ({'firmware_revision': 0x0300}, {'firmware_revision': 0x0301},
                        {'protocol_version': 2}, {'host_confirmed': False}):
                original = dict(self.dock.snapshot['controller_status'])
                self.dock.snapshot['controller_status'].update(bad)
                self.assertFalse(self.dock.psp_launcher_input()['connected'])
                self.dock.snapshot['controller_status'] = original
            reader.return_value.read.assert_called_once()


if __name__ == '__main__':
    unittest.main()
