"""One-cable file transfer PROTOCOL tests; no PSP USB hardware access."""
from pathlib import Path
import os
import struct
import tempfile
import unittest

from psp_bulk_protocol import (
    BulkProtocolError,Frame,Op,MAX_PAYLOAD,
    InProcessPSPFileServer,SimulatedHost,validated_path,
)


SESSION=b'\x04'*16


class PSPBulkProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.psp=self.root/'synthetic-memory-stick'
        self.psp.mkdir()
        self.iso=self.psp/'ISO/Patapon.iso'
        self.iso.parent.mkdir()
        self.iso.write_bytes(b'P'*114_291+b'\0hello')
        self.save=self.psp/'PSP/SAVEDATA/SLOT1/DATA.BIN'
        self.save.parent.mkdir(parents=True)
        self.save.write_bytes(b'UNIQUE_SAVE')
        self.server=InProcessPSPFileServer(self.psp,volume_serial='2958455453')

    def tearDown(self):
        self.temp.cleanup()

    def test_frame_v1_roundtrip_and_crc_tamper(self):
        f=Frame(Op.READ,SESSION,1,b'\x10\x00\x00\x00ISO/Patapon.iso',42)
        wire=f.encode()
        self.assertEqual(Frame.decode(wire),f)
        broken=wire[:-1]+bytes([wire[-1]^1])
        with self.assertRaisesRegex(BulkProtocolError,'CRC'):
            Frame.decode(broken)
        with self.assertRaises(BulkProtocolError):
            Frame.decode(wire[:-1])
        with self.assertRaises(BulkProtocolError):
            Frame(Op.DATA,SESSION,2,b'A'*(MAX_PAYLOAD+1)).encode()

    def test_path_allowlist_refuses_firmware_flash_and_traversal(self):
        for name in ('../flash0:/kd/usb.prx','ISO/../ISO/game.iso',
                     'PSP/SAVEDATA/../../flash0/a','C:/temp.iso',
                     '//PSP/ISO/escape.iso','ISO\\\\file.iso',
                     'PSP/SYSTEM/ppsspp.ini','PSP/GAME/../user/EBOOT.PBP'):
            with self.subTest(name=name),self.assertRaises(BulkProtocolError):
                validated_path(name)
        self.assertEqual(validated_path('ISO/Patapon.iso'),Path('ISO/Patapon.iso'))
        self.assertEqual(validated_path('PSP/SAVEDATA/SLOT1/DATA.BIN'),
                         Path('PSP/SAVEDATA/SLOT1/DATA.BIN'))

    def test_host_downloads_a_game_without_mounting_psp_fat_on_pc(self):
        host=SimulatedHost(self.server,session=SESSION,
                           expected_serial='2958455453')
        destination=self.root/'private-PC-library/ISO/Patapon.iso'
        result=host.copy_game_readonly('ISO/Patapon.iso',destination,
                                       chunk_bytes=4096)
        self.assertEqual(destination.read_bytes(),self.iso.read_bytes())
        self.assertEqual(result['bytes'],self.iso.stat().st_size)
        self.assertFalse(result['physical_usb_proven'])
        self.assertEqual(self.save.read_bytes(),b'UNIQUE_SAVE')
        with self.assertRaises(BulkProtocolError):
            host.copy_game_readonly('ISO/Patapon.iso',destination)

    def test_reject_unknown_serial_session_replay_and_unapproved_write(self):
        with self.assertRaises(BulkProtocolError):
            SimulatedHost(self.server,session=SESSION,expected_serial='12345')
        host=SimulatedHost(self.server,session=SESSION,
                          expected_serial='2958455453')
        packet=Frame(Op.STAT,SESSION,2,b'ISO/Patapon.iso').encode()
        response=self.server.reply(packet)
        self.assertEqual(Frame.decode(response).op,Op.META)
        with self.assertRaisesRegex(BulkProtocolError,'sesión|secuencia'):
            self.server.reply(packet)
        with self.assertRaisesRegex(BulkProtocolError,'sesión|secuencia'):
            self.server.reply(Frame(Op.STAT,b'\x05'*16,3,b'ISO/Patapon.iso').encode())
        with self.assertRaisesRegex(BulkProtocolError,'solo lectura'):
            self.server.reply(Frame(Op.ERROR,SESSION,3,b'ANYTHING').encode())

    def test_symlink_outside_psp_is_rejected(self):
        outside=self.root/'secret'
        outside.write_bytes(b'NOT-A-GAME')
        pointer=self.psp/'ISO/foreign.iso'
        try:
            pointer.symlink_to(outside)
        except (OSError,NotImplementedError):
            self.skipTest('Symlinks unavailable')
        host=SimulatedHost(self.server,session=SESSION,
                          expected_serial='2958455453')
        with self.assertRaises(BulkProtocolError):
            host.copy_game_readonly('ISO/foreign.iso',self.root/'stolen.iso')


if __name__=="__main__":
    unittest.main()
