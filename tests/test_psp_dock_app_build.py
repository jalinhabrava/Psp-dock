"""Product HID source must be permanent, isolated and fail-safe."""
from pathlib import Path
import unittest


ROOT=Path(__file__).resolve().parents[1]/'homebrew'


class PersistentHidBuildContractTests(unittest.TestCase):
    def test_product_has_no_fixed_duration_and_explicit_exit(self):
        product=ROOT/'psp-dock-app'
        user=(product/'main.c').read_text(encoding='utf-8')
        core=(product/'lab_core.c').read_text(encoding='utf-8')
        self.assertNotIn('TIMEOUT_US',user+core)
        self.assertNotIn('elapsed <',user+core)
        self.assertNotIn('Auto-stop at',user+core)
        self.assertIn('for (;;)',user)
        self.assertIn('for (;;)',core)
        self.assertIn('START+SELECT',user)
        self.assertIn('PSP_CTRL_START | PSP_CTRL_SELECT',core)
        self.assertIn('else if (cable_seen)',core)
        self.assertIn('stop_lab();',core)
        self.assertIn('PSPDock/bridgehid.prx',user)
        self.assertIn('PSPDock/dockcore.prx',user)
        self.assertIn('PSP Dock', (product/'Makefile').read_text())
        ui=(product/'ui.c').read_text(encoding='utf-8')
        self.assertIn('PREPARA TU DOCK',ui)
        self.assertIn('Desconecta el cable USB.',ui)
        self.assertIn('CONECTA LA PSP',ui)
        self.assertIn('USB_GUARD_TICKS',user)
        self.assertIn('CONNECT_GUIDE_TICKS',user)
        self.assertNotIn('pspDebugScreenPrintf',user)
        self.assertIn('PSP_CTRL_CROSS',user)
        self.assertIn('sceKernelExitGame()',user)
        self.assertLess(user.index('ui.stage = DOCK_UI_WELCOME'),
                        user.index('load_and_start(HID_MODULE)'))

    def test_same_verified_gate_and_driver_not_modified(self):
        product=ROOT/'psp-dock-app'
        original=ROOT/'hid-only-lab'
        for file in ('lab_gate.h','PSPDockHid.S','exports.exp'):
            self.assertEqual((product/file).read_bytes(),(original/file).read_bytes())
        original_core=(original/'lab_core.c').read_text()
        self.assertIn('#define TIMEOUT_US 90000000u',original_core)
        self.assertIn('90000000u',original_core)

    def test_build_never_installs_to_psp(self):
        path=ROOT/'psp-dock-app/build-wsl.sh'
        script=path.read_text(encoding='utf-8')
        self.assertIn('psp-dock-app',script)
        self.assertIn('Unexpected fixed-duration watchdog',script)
        self.assertNotIn('ms0:/',script)
        self.assertNotIn('flash0:',script)
        self.assertNotIn('PSPDockHidOnly',script)


if __name__=='__main__':
    unittest.main()
