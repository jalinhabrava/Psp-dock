import unittest
from unittest.mock import patch
import xmb_window as xmb

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

    def test_wait_does_not_spend_more_time_than_requested(self):
        with self.assertRaises(ValueError):
            xmb.maximize_when_ready(seconds=0)
        with self.assertRaises(ValueError):
            xmb.maximize_when_ready(interval=0)


if __name__=='__main__':
    unittest.main()
