from contextlib import contextmanager
import unittest

from psp_dock.core.windows_storage_identity import physical_disk


class FakeApi:
    def __init__(self):self.calls=[];self.changed=False;self.duplicate=False;self.reads=0
    @contextmanager
    def open(self,path):
        self.calls.append(('open',path))
        try:yield path
        finally:self.calls.append(('close',path))
    def number(self,handle):
        if handle.startswith('\\\\.'):
            self.reads+=1
            return (7,9 if self.changed and self.reads>1 else 6)
        return (7,6)
    def disks(self):
        yield ('disk-interface','USBSTOR\\DISK&VEN_SONY&PROD_PSP\\1','SONY PSP')
        if self.duplicate:yield ('disk-interface-2','USBSTOR\\DISK&VEN_SONY&PROD_PSP\\2','SONY PSP')


class StorageIdentityTests(unittest.TestCase):
    def test_fresh_association_reads_both_handles_and_rechecks_volume(self):
        api=FakeApi();result=physical_disk('G:\\',api=api)
        self.assertEqual(result,('USBSTOR\\DISK&VEN_SONY&PROD_PSP\\1','SONY PSP'))
        self.assertEqual(api.reads,2)
        self.assertEqual(sum(a[0]=='open' for a in api.calls),sum(a[0]=='close' for a in api.calls))
    def test_swap_or_ambiguity_cannot_select_a_disk(self):
        for flag in ('changed','duplicate'):
            api=FakeApi();setattr(api,flag,True)
            with self.subTest(flag=flag),self.assertRaises(ValueError):physical_disk('G:\\',api=api)
            self.assertEqual(sum(a[0]=='open' for a in api.calls),sum(a[0]=='close' for a in api.calls))
    def test_invalid_drive_is_rejected_before_open(self):
        api=FakeApi()
        with self.assertRaises(ValueError):physical_disk('G:\\PSP',api=api)
        self.assertEqual(api.calls,[])
