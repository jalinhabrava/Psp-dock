"""PSP detection → opt-in background installer, never network in tests."""
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock
from retro_installer import managed_retroarch
from psp_retro import SYSTEMS


class InstallerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.psp=self.root/'synthetic-sony-psp'
        self.homebrew=self.psp/'PSP/GAME/gpSP'
        (self.homebrew/'ROMS').mkdir(parents=True)
        (self.homebrew/'EBOOT.PBP').write_bytes(b'synthetic emulator')
        (self.homebrew/'ROMS/Example.gba').write_bytes(b'test rom, never executed')
        (self.psp/'PSP/SAVEDATA').mkdir(parents=True)
        self.no_devices=patch('dock.windows_devices',return_value=[])
        self.no_devices.start()
        self.addCleanup(self.no_devices.stop)
        self.dock=Dock(self.root/'data')
        self.dock.settings({'psp_root':str(self.psp)})
        # Keep synthetic fixtures offline except in tests explicitly checking
        # auto mode, where the downloader is mocked.
        self.dock.settings({'retro_install_policy':'ask'})

    def test_manual_policy_does_not_download_from_a_connection(self):
        with patch('dock.install_pc_retro',side_effect=AssertionError('unexpected installer')) as install:
            self.dock.retro_catalog()
            self.assertEqual(self.dock.retro_install_state()['policy'],'ask')
            install.assert_not_called()
        self.assertFalse(managed_retroarch(self.dock.config['storage_root']).exists())

    def test_manual_install_uses_selected_storage_and_commits_only_after_success(self):
        self.dock.retro_catalog()
        completed=threading.Event()
        managed=managed_retroarch(self.dock.config['storage_root'])
        def fake_install(root,emulators,**kwargs):
            self.assertEqual(Path(root),Path(self.dock.config['storage_root']))
            self.assertEqual(emulators[0]['systems'],['gba'])
            managed.parent.mkdir(parents=True)
            managed.write_bytes(b'SYNTHETIC-NOT-EXECUTABLE')
            (managed.parent/'cores').mkdir()
            (managed.parent/'cores'/SYSTEMS['gba'][1]).write_bytes(b'SYNTHETIC CORE')
            completed.set()
            return {'installed_cores':[SYSTEMS['gba'][1]],'downloaded_bytes':0}
        with patch('dock.install_pc_retro',side_effect=fake_install) as install:
            self.assertTrue(self.dock.start_retro_install())
            self.assertTrue(completed.wait(4))
            # Wait until worker has committed configuration, not merely the
            # synthetic fake downloader's completion event.
            for _ in range(400):
                with self.dock.retro_installer_lock:
                    active=self.dock.retro_install_progress['active']
                if not active:break
                threading.Event().wait(.01)
            self.assertFalse(active)
            install.assert_called_once()
        self.assertEqual(self.dock.config['retroarch'],str(managed))
        self.assertTrue(self.dock.retro_install_state()['retroarch_installed'])
        self.assertEqual(self.dock.retro_install_state()['status'],'ready')
        self.assertEqual(self.dock.retro_install_progress['phase'],'ready')
        self.assertFalse(self.dock.start_retro_install())

    def test_auto_mode_schedules_once_when_homebrew_is_detected(self):
        self.dock.settings({'retro_install_policy':'auto'})
        with patch.object(self.dock,'start_retro_install',return_value=True) as start:
            self.dock.retro_catalog()
            self.assertTrue(start.called)
            self.assertEqual(start.call_args.kwargs,{'automatic':True})

    def test_cached_inventory_permits_pc_install_without_usb_or_psp_io(self):
        self.dock.retro_catalog()
        completed=threading.Event()
        original_rom=(self.homebrew/'ROMS/Example.gba').read_bytes()
        def fake_install(*args,**kwargs):
            completed.set()
            return {'installed_cores':[]}
        with (patch.object(self.dock,'root',return_value=None),
              patch('dock.install_pc_retro',side_effect=fake_install) as install,
              patch.object(self.dock,'retro_save_plan',side_effect=AssertionError('save IO')),
              patch.object(self.dock,'transfer_retro',side_effect=AssertionError('ROM copy'))):
            self.assertTrue(self.dock.start_retro_install())
            self.assertTrue(completed.wait(4))
            for _ in range(400):
                if not self.dock.retro_install_progress['active']:break
                threading.Event().wait(.01)
            self.assertFalse(self.dock.retro_install_progress['active'])
            install.assert_called_once()
        self.assertEqual((self.homebrew/'ROMS/Example.gba').read_bytes(),original_rom)

    def test_default_auto_and_explicit_off(self):
        fresh=Dock(self.root/'fresh')
        self.assertEqual(fresh.config['retro_install_policy'],'auto')
        fresh.settings({'psp_root':str(self.psp),'retro_install_policy':'off'})
        with patch('dock.install_pc_retro',side_effect=AssertionError('network')):
            fresh.retro_catalog()
            self.assertFalse(fresh.start_retro_install())

    def test_hid_auto_uses_cached_serial_and_keeps_xmb_cache_fresh(self):
        self.dock.retro_catalog()
        self.dock.settings({'retro_install_policy':'auto'})
        managed=managed_retroarch(self.dock.config['storage_root'])
        done=threading.Event()
        def install(*args,**kwargs):
            managed.parent.mkdir(parents=True,exist_ok=True)
            managed.write_bytes(b'SYNTHETIC-EXE')
            core=managed.parent/'cores'/SYSTEMS['gba'][1]
            core.parent.mkdir(exist_ok=True)
            core.write_bytes(b'SYNTHETIC-CORE')
            done.set()
            return {'installed_cores':[core.name]}
        with (patch.object(self.dock,'root',return_value=None),
              patch('dock.install_pc_retro',side_effect=install) as installer):
            self.assertFalse(self.dock.cached_retro_results['roms'][0]['core_available'])
            self.assertTrue(self.dock.start_retro_install(automatic=True))
            self.assertTrue(done.wait(4))
            for _ in range(400):
                if not self.dock.retro_install_progress['active']:break
                threading.Event().wait(.01)
            self.assertFalse(self.dock.retro_install_progress['active'])
            self.assertTrue(self.dock.cached_retro_results['roms'][0]['core_available'])
            self.assertFalse(self.dock.start_retro_install(automatic=True))
            installer.assert_called_once()

    def test_dont_modify_external_retroarch_or_swallow_network_error(self):
        self.dock.retro_catalog()
        external=self.root/'external/retroarch.exe'
        external.parent.mkdir(parents=True)
        external.write_bytes(b'EXTERNAL USER FILE')
        self.dock.settings({'retroarch':str(external)})
        with self.assertRaisesRegex(ValueError,'externa'):
            self.dock.start_retro_install()
        self.assertEqual(external.read_bytes(),b'EXTERNAL USER FILE')

    def test_failed_download_is_visible_and_does_not_set_exe(self):
        self.dock.retro_catalog()
        signalled=threading.Event()
        def fail(*args,**kwargs):
            signalled.set()
            raise OSError('synthetic network unreachable')
        with patch('dock.install_pc_retro',side_effect=fail):
            self.assertTrue(self.dock.start_retro_install())
            self.assertTrue(signalled.wait(4))
            for _ in range(400):
                with self.dock.retro_installer_lock:
                    active=self.dock.retro_install_progress['active']
                if not active:break
                threading.Event().wait(.01)
        self.assertEqual(self.dock.retro_install_progress['phase'],'failed')
        self.assertIn('network unreachable',self.dock.retro_install_progress['error'])
        self.assertEqual(self.dock.config['retroarch'],'')

    def test_failed_auto_install_does_not_loop_but_manual_retry_is_allowed(self):
        self.dock.retro_catalog()
        self.dock.settings({'retro_install_policy':'auto'})
        calls=[]
        def fail(*args,**kwargs):
            calls.append('attempt')
            raise OSError('offline test')
        with (patch.object(self.dock,'root',return_value=None),
              patch('dock.install_pc_retro',side_effect=fail)):
            self.assertTrue(self.dock.start_retro_install(automatic=True))
            for _ in range(400):
                if self.dock.retro_install_progress['phase']=='failed' and not self.dock.retro_install_progress['active']:
                    break
                threading.Event().wait(.01)
            self.assertEqual(len(calls),1)
            self.assertFalse(self.dock.start_retro_install(automatic=True))
            self.assertTrue(self.dock.start_retro_install(automatic=False))
            for _ in range(400):
                if len(calls)==2 and not self.dock.retro_install_progress['active']:
                    break
                threading.Event().wait(.01)
            self.assertEqual(len(calls),2)

    def test_active_session_does_not_install_even_with_cached_hid_inventory(self):
        self.dock.retro_catalog()
        self.dock.settings({'retro_install_policy':'auto'})
        with (patch.object(self.dock,'root',return_value=None),
              patch.object(self.dock,'running',return_value=True),
              patch('dock.install_pc_retro',side_effect=AssertionError('unexpected install'))):
            self.assertFalse(self.dock.start_retro_install(automatic=True))


if __name__=='__main__':
    unittest.main()
