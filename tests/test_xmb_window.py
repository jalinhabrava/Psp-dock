import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
import psp_dock.core.xmb_window as xmb
from dock import launch_xmb_window, webview_shell_executable

TITLE=xmb.TITLE
GOOD={'handle': 333, 'title':TITLE,
      'window_class':'Chrome_WidgetWin_1','visible':True}


class XmbWindowTests(unittest.TestCase):
    def test_only_unique_exact_app_window_is_selected(self):
        self.assertEqual(xmb.unique_target([GOOD]),333)
        self.assertIsNone(xmb.unique_target([]))
        self.assertIsNone(xmb.unique_target([GOOD,dict(GOOD,handle=444)]))
        for change in ({
            'title':TITLE+' - Google Chrome',
        }, {'window_class':'WindowsForms10.Window.8.app'},
            {'visible':False}, {'handle':0}):
            with self.subTest(change=change):
                self.assertIsNone(xmb.unique_target([dict(GOOD,**change)]))

    def test_nonwindows_cannot_control_desktop(self):
        with patch.object(xmb.os,'name','posix'):
            self.assertFalse(xmb.maximize_existing_xmb())
            self.assertFalse(xmb.maximize_when_ready(seconds=0.1))

    def test_only_a_verified_webview_shell_process_can_be_reused(self):
        native=dict(GOOD,handle=888,window_class='WindowsForms10.Window.8.app.0.1',
                    process_name='PspDockWebShell.exe')
        self.assertEqual(xmb.unique_target([native]),888)
        self.assertIsNone(xmb.unique_target([dict(native,process_name='other.exe')]))
        self.assertIsNone(xmb.unique_target([dict(native,process_name='')]))
        self.assertIsNone(xmb.unique_target([GOOD,native]),
                          'Two app windows are ambiguous: do not close either')

    def test_launch_native_shell_uses_only_an_explicit_verified_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell = Path(tmp)/'PspDockWebShell.exe'
            shell.write_bytes(b'test fixture, never executed')
            with patch.dict('os.environ',{'PSP_DOCK_WEBVIEW2_SHELL':str(shell)}):
                self.assertEqual(webview_shell_executable(),shell)
            with patch('dock.webview_shell_executable',return_value=shell), \
                 patch.object(xmb,'enumerate_xmb_windows',return_value=[]), \
                 patch('dock.subprocess.Popen') as spawn, \
                 patch('dock.threading.Thread'):
                launch_xmb_window('http://127.0.0.1:59336',app_window=True,reuse_existing=False)
            self.assertEqual(spawn.call_args.args[0],[str(shell),'http://127.0.0.1:59336'])

    def test_wait_does_not_spend_more_time_than_requested(self):
        with self.assertRaises(ValueError):
            xmb.maximize_when_ready(seconds=0)
        with self.assertRaises(ValueError):
            xmb.maximize_when_ready(interval=0)

    def test_orphaned_title_is_excluded_from_new_window_selection(self):
        fresh=dict(GOOD,handle=444)
        self.assertEqual(xmb.unique_target([GOOD,fresh],exclude_handles={333}),444)
        self.assertIsNone(xmb.unique_target([GOOD],exclude_handles={333}))
        with patch('dock.Path.is_file',return_value=True), \
             patch('dock.subprocess.Popen') as spawn, \
             patch('dock.threading.Thread') as thread, \
             patch.object(xmb,'enumerate_xmb_windows',return_value=[GOOD]), \
             patch.object(xmb,'maximize_existing_xmb',return_value=True) as reuse:
            launch_xmb_window('http://127.0.0.1:59336',app_window=True,
                              reuse_existing=False)
        reuse.assert_not_called()
        self.assertIn('--app=http://127.0.0.1:59336',spawn.call_args.args[0])
        self.assertEqual(thread.call_args.kwargs['kwargs']['exclude_handles'],{333})

    def test_reuse_requires_the_server_observed_handle(self):
        owned=dict(GOOD,handle=444)
        self.assertEqual(xmb.unique_target([GOOD,owned],include_handles={444}),444)
        self.assertIsNone(xmb.unique_target([GOOD],include_handles={444}))
        self.assertIsNone(xmb.unique_target([GOOD],include_handles=set()))
        with patch.object(xmb,'maximize_existing_xmb',return_value=True) as focus, \
             patch('dock.subprocess.Popen') as spawn:
            launch_xmb_window('http://127.0.0.1:59336',app_window=True,
                              reuse_existing=True,include_handles={444})
        focus.assert_called_once_with(include_handles={444})
        spawn.assert_not_called()


if __name__=='__main__':
    unittest.main()
