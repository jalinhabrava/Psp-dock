import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import tools.system.on_psp_hid_arrival as entry
import psp_dock.core.setup_usb_event as setup


class PnpEventTaskTests(unittest.TestCase):
    def test_xml_runs_only_a_short_hid_validator_on_event_without_boot_trigger(self):
        with tempfile.TemporaryDirectory() as base:
            root=Path(base)
            xml=setup.task_xml(python_exe=root/"pythonw.exe",
                               event_script=root/"tools/system/on_psp_hid_arrival.py",
                               user_sid="S-1-5-21-100-200-300-400")
        self.assertIn("Kernel-PnP/Configuration",xml)
        self.assertIn("EventID=410",xml)
        self.assertNotIn("BootTrigger",xml)
        self.assertNotIn("LogonTrigger",xml)
        self.assertNotIn("AutoRun",xml)
        self.assertIn("InteractiveToken",xml)
        self.assertIn("LeastPrivilege",xml)
        self.assertIn("PT0S",xml)
        ET.fromstring(xml)

    def test_script_verifies_exact_psp_hid_and_never_autoplays_games(self):
        values=iter([
            {"connected":False,"source":"psp_winmm"},
            {"connected":True,"source":"foreign"},
            {"connected":True,"source":"psp_winmm"},
        ])
        times=iter([0,0.25,0.5,0.75,1.0,1.25])
        self.assertTrue(entry.wait_for_psp_hid(lambda:next(values),
            seconds=1.4,now=lambda:next(times),sleep=lambda x:None))
        with patch.object(entry.os,"name","nt"):
            launches=[]
            ok=entry.handle_arrival(
                probe=lambda:{"connected":True,"source":"psp_winmm"},
                launch=lambda:launches.append("open XMB"),wait_seconds=1)
        self.assertTrue(ok)
        self.assertEqual(launches,["open XMB"])

    def test_no_event_does_not_launch_any_windows_program(self):
        with patch.object(entry.os,"name","nt"):
            calls=[]
            self.assertFalse(entry.handle_arrival(
                probe=lambda:{"connected":False,"source":"psp_winmm"},
                launch=lambda:calls.append("unexpected"),wait_seconds=0.1))
            self.assertEqual(calls,[])

    def test_xml_rejects_untrusted_app_paths_or_user(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            for script in ("other.py",'on_psp_hid_arrival".py'):
                with self.subTest(script=script),self.assertRaises(ValueError):
                    setup.task_xml(python_exe=directory/"pythonw.exe",
                                   event_script=directory/script,
                                   user_sid="S-1-5-21-9-8-7-6")


if __name__=="__main__":
    unittest.main()
