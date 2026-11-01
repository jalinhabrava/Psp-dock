"""Source contract checks supplement, never replace, ELF/native/hardware tests."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'homebrew/composite-readonly'


class ReadOnlyFirmwareSafetyTests(unittest.TestCase):
    def test_callbacks_never_touch_filesystem(self):
        driver = (BASE / 'pdc_driver.c').read_text(encoding='utf-8')
        callback_region = driver[driver.index('static int pad_done'):driver.index('static struct UsbDriver')]
        self.assertNotIn('sceIo', callback_region)
        self.assertNotIn('pdc_service_process', callback_region)

    def test_only_read_mode_and_no_flash_or_file_mutation_calls(self):
        adapter = (BASE / 'pdc_psp_fs.c').read_text(encoding='utf-8')
        # The source comment lists intentionally absent APIs; inspect code,
        # not that explanatory sentence. Linked symbols are audited too.
        adapter = re.sub(r'/\*.*?\*/|//[^\n]*', '', adapter, flags=re.S)
        self.assertIn('PSP_O_RDONLY', adapter)
        for name in ('sceIoWrite', 'sceIoRemove', 'sceIoRename', 'sceIoDevctl',
                     'PSP_O_WRONLY', 'PSP_O_RDWR', 'PSP_O_CREAT', 'flash0:'):
            self.assertNotIn(name, adapter)
        service = (BASE / 'pdc_service.c').read_text(encoding='utf-8')
        self.assertNotIn('PDB2_OP_WRITE', service)
        self.assertNotIn('PDB2_OP_DELETE', service)

    def test_registration_only_after_no_storage_gate_and_buffer_drain(self):
        driver = (BASE / 'pdc_driver.c').read_text(encoding='utf-8')
        worker = driver[driver.index('static int io_worker'):driver.index('static int valid_shared')]
        self.assertLess(worker.index('hid_lab_safe_to_start'), worker.index('sceUsbbdRegister'))
        self.assertLess(worker.index('USBSTOR'), worker.index('pdc_service_process'))
        entry = driver[driver.index('int module_start'):driver.index('int module_stop')]
        self.assertNotIn('sceUsbbdRegister(', entry)
        cleanup = driver[driver.index('static int stop_usb'):driver.index('static int io_worker')]
        self.assertLess(cleanup.index('ctl_submitting'), cleanup.index('sceUsbbdReqCancelAll'))
        self.assertLess(cleanup.index('hid_busy||ctl_busy||rx_busy||tx_busy'),
                        cleanup.index('sceUsbStop(PDC_DRIVER_NAME'))

    def test_game_session_not_ended_by_artificial_lab_watchdog(self):
        driver = (BASE / 'pdc_driver.c').read_text(encoding='utf-8')
        app = (BASE / 'app.c').read_text(encoding='utf-8')
        for pattern in ('TIMEOUT_US', 'elapsed <', '90000000u', '900000000u'):
            self.assertNotIn(pattern, driver + app)
        self.assertIn('PSP_CTRL_START|PSP_CTRL_SELECT', driver)
        self.assertIn('if(ever_connected)stop_requested=1', driver)
        self.assertIn('PSPDockCompositeTest/dockcomposite.prx', app)
        self.assertNotIn('PSP/GAME/PSPDock/', app)

    def test_kernel_abi_has_independent_settings_for_both_interfaces(self):
        desc = (BASE / 'pdc_descriptors.h').read_text(encoding='utf-8')
        for speed in ('hi', 'full'):
            self.assertIn('PdcSettings pdc_settings_' + speed + '[2]', desc)
            self.assertIn('{&pdc_interfaces_' + speed + '[0],0,1}', desc)
            self.assertIn('{&pdc_interfaces_' + speed + '[1],0,1}', desc)
        self.assertNotIn('PdcInterface *descriptors[2]', desc)


if __name__ == '__main__':
    unittest.main()
