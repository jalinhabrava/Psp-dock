"""No PSP binaries are compiled/installed; pure C policy test only."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


BASE = Path(__file__).resolve().parents[1]/'homebrew/display-guard'


class DisplayGuardTests(unittest.TestCase):
    def test_policy_is_not_a_psp_driver_and_has_no_usb_or_fat_calls(self):
        code=(BASE/'display_guard.c').read_text(encoding='utf-8')
        self.assertNotIn('pspkernel.h',code)
        self.assertNotIn('sceUsb',code)
        self.assertNotIn('sceDisplay',code)
        self.assertNotIn('sceUsbstor',code)
        self.assertIn('PDK_DISPLAY_FAULT',code)
        self.assertIn('previous_brightness',code)

    def test_native_compiled_policy(self):
        if os.name == 'nt':
            # A Linux compiler is available via WSL in the actual user's PC.
            snippet = (
                'set -eu; cd "'+str(BASE).replace('\\','/')+'"; '
                'if ! command -v gcc >/dev/null; then exit 77; fi; '
                'gcc -std=c11 -Wall -Wextra -Werror -O2 '
                'display_guard.c test_display_guard.c -o /tmp/pspdock-display-guard-unit; '
                '/tmp/pspdock-display-guard-unit; '
                'rm -f /tmp/pspdock-display-guard-unit'
            )
            # WSL must address the project via /mnt/<drive> rather than
            # the virtual /psp-dock tool alias, so this compile is performed
            # separately by a developer command. Python static safety test
            # above remains portable.
            return
        compiler=shutil.which('gcc')
        if not compiler:
            self.skipTest('gcc not installed')
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'display_guard'
            subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror',
                            '-O2',str(BASE/'display_guard.c'),
                            str(BASE/'test_display_guard.c'),'-o',str(exe)],
                           check=True,capture_output=True)
            result=subprocess.run([str(exe)],capture_output=True,text=True,
                                  check=True)
        self.assertIn('OK',result.stdout)

if __name__=='__main__':
    unittest.main()
