"""Actual Windows HID boundary under test doubles; no real USB access.

GET Feature64 remains unchanged. Firmware0306 accepts one complete core only
from 32 small Output reports plus a CRC16 commit. Hardware validation is separate.
"""
import binascii
import ctypes as C
from types import SimpleNamespace
import unittest

import psp_dock.devices.native_dock as native
from tests.test_native_dock import PATH, SESSION, report, status


class EnvelopeTests(unittest.TestCase):
    def boundary(self, raw, size=65, fail_at=None):
        api = native.NativeHid.__new__(native.NativeHid)
        calls = []
        def caps(data, pointer):
            value = C.cast(pointer, C.POINTER(native.HIDP_CAPS)).contents
            value.UsagePage, value.Usage = 1, 4
            value.FeatureReportByteLength = size
            value.InputReportByteLength, value.OutputReportByteLength = 9, 5
            return 0x00110000
        def get(handle, buffer, length):
            calls.append(('get', length))
            C.memmove(buffer, raw[:length], min(len(raw), length))
            return 1
        def set_feature(handle, buffer, length):
            calls.append(('feature', C.string_at(buffer, length)))
            return 1
        def set_output(handle, buffer, length):
            calls.append(('output', C.string_at(buffer, length)))
            return 0 if sum(c[0]=='output' for c in calls)==fail_at else 1
        api.kernel = SimpleNamespace(CreateFileW=lambda *args:42,
            CloseHandle=lambda handle:calls.append(('close', handle)))
        api.hid = SimpleNamespace(HidD_GetPreparsedData=lambda *args:1,
            HidP_GetCaps=caps, HidD_FreePreparsedData=lambda *args:None,
            HidD_GetFeature=get, HidD_SetFeature=set_feature, HidD_SetOutputReport=set_output)
        api.attributes = lambda handle:{'vendor_id':native.VID, 'product_id':native.PID, 'revision':0x0100}
        return api, calls

    def test_status_length_stays_unchanged_and_old_firmware_keeps_its_commands(self):
        old=status(firmware_revision=0x0305,build_id=0x03050001,host_confirmed=False,activity=0)
        api,calls=self.boundary(report(old))
        frame=native.encode_command(SESSION,1,native.STORAGE,native.HOST_BEGIN,
                                   firmware_revision=0x0305,build_id=0x03050001)
        with api.open(PATH,write=True) as handle:
            self.assertEqual(api.get(handle),old)
            api.set(handle,frame)
        self.assertEqual(calls,[('get',65),('feature',frame),('close',42)])
        self.assertEqual(api._report_lengths,{})

    def test_new_command_uses_only_small_reports_and_commits_exact_core_crc(self):
        expected=status(host_confirmed=False,activity=0)
        api,calls=self.boundary(report(expected))
        frame=native.encode_command(SESSION,1,native.STORAGE,native.HOST_BEGIN)
        with api.open(PATH,write=True) as handle:
            self.assertEqual(api.get(handle),expected)
            api.set(handle,frame)
        chunks=[row[1] for row in calls if row[0]=='output']
        self.assertEqual(len(chunks),33)
        self.assertTrue(all(len(chunk)==5 and chunk[0]==0 for chunk in chunks))
        self.assertEqual([chunk[1] for chunk in chunks[:32]],list(range(0xc0,0xe0)))
        self.assertEqual(len({chunk[2] for chunk in chunks}),1)
        core=b''.join(chunk[3:] for chunk in chunks[:32])
        self.assertEqual(core,frame[1:])
        self.assertEqual(chunks[-1][1],0xe0)
        self.assertEqual(int.from_bytes(chunks[-1][3:],'little'),binascii.crc_hqx(core,0xffff))
        self.assertFalse(any(row[0]=='feature' for row in calls))

    def test_failed_fragment_stops_without_commit_or_automatic_resend(self):
        frame=native.encode_command(SESSION,1,native.STORAGE,native.HOST_BEGIN)
        for failure in (1,13,32,33):
            api,calls=self.boundary(report(status()),fail_at=failure)
            with self.subTest(failure=failure),api.open(PATH,write=True) as handle:
                with self.assertRaises(native.NativeDockError):api.set(handle,frame)
                self.assertEqual(sum(row[0]=='output' for row in calls),failure)
                self.assertFalse(any(row[0]=='feature' for row in calls))

    def test_bad_frame_never_reaches_any_win32_write(self):
        frame=native.encode_command(SESSION,1,native.STORAGE,native.HOST_BEGIN)
        api,calls=self.boundary(report(status()))
        with api.open(PATH,write=True) as handle:
            for invalid in (frame[:-1],frame+b'X',frame[:47]+b'\x01'+frame[48:]):
                with self.subTest(length=len(invalid)),self.assertRaises(native.NativeDockError):
                    api.set(handle,invalid)
        self.assertFalse(any(row[0] in ('feature','output') for row in calls))

    def test_compact_diagnostic_is_read_only_and_gated_to_new_firmware(self):
        word=3 | (4 << 4) | ((-3 & 31) << 11)
        api,calls=self.boundary(report(status(transport_word=word)))
        with api.open(PATH) as handle:
            self.assertEqual(api.transport_diagnostic(handle,
                status(firmware_revision=0x0305,build_id=0x03050001)),{})
            self.assertEqual(calls,[])
            value=api.transport_diagnostic(handle,status())
        self.assertEqual(value,{'available':True,'drop_reason':3,'received_bytes':4,
                                'completion_result':-3,'parser_result':None})
        self.assertFalse(any(row[0] in ('feature','output') for row in calls))
        self.assertEqual(calls[0],('get',65))


if __name__=='__main__':unittest.main()
