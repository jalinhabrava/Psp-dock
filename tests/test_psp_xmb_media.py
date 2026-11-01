import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from psp_artwork import SECTOR
from psp_xmb_media import (
    XMBAssetCache, convert_at3_to_mp3, media_key, read_resource, valid_at3,
)
from tests.test_psp_artwork import PNG, rec, write_fixture_iso, write_fixture_cso


def sound_fixture():
    # Fixture validates RIFF container only. Do not ship copyrighted sound.
    fmt=struct.pack('<HHIIHH',0x270,2,44100,6000,256,16)
    data=b'\xaa\xbb' * 20
    body=b'WAVE'+b'fmt '+struct.pack('<I',len(fmt))+fmt + b'data'+struct.pack('<I',len(data))+data
    return b'RIFF'+struct.pack('<I',len(body))+body


class XMBMediaTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.root=Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_iso_allows_background_overlay_and_sound(self):
        iso=self.root/'game.iso'
        write_fixture_iso(iso)
        media={"PIC0.PNG":PNG,"PIC1.PNG":PNG,"SND0.AT3":sound_fixture()}
        raw=bytearray(iso.read_bytes())
        directory_offset=21*SECTOR
        offset=len(rec(b'ICON0.PNG;1',22,len(PNG),False))
        for name, payload in media.items():
            sector={"PIC0.PNG":23,"PIC1.PNG":24,"SND0.AT3":25}[name]
            directory=rec((name+';1').encode('ascii'),sector,len(payload),False)
            raw[directory_offset+offset:directory_offset+offset+len(directory)]=directory
            offset+=len(directory)
            raw[sector*SECTOR:sector*SECTOR+len(payload)]=payload
        iso.write_bytes(raw)
        self.assertEqual(read_resource(iso,'icon'),PNG)
        self.assertEqual(read_resource(iso,'overlay'),PNG)
        self.assertEqual(read_resource(iso,'background'),PNG)
        self.assertEqual(read_resource(iso,'sound'),sound_fixture())
        cso=self.root/'game.cso'
        write_fixture_cso(cso,bytes(raw))
        self.assertEqual(read_resource(cso,'background'),PNG)
        self.assertEqual(read_resource(cso,'sound'),sound_fixture())

    def test_pbp_fixed_sections_and_sound_within_limits(self):
        pbp=self.root/'EBOOT.PBP'
        sfo=b'\0PSF'+b'\0'*16
        sections=[sfo,PNG,b'',PNG,PNG,sound_fixture(),b'',b'']
        offsets=[]
        cursor=40
        for part in sections:
            offsets.append(cursor)
            cursor+=len(part)
        pbp.write_bytes(b'\0PBP'+b'\0\0\1\0'+struct.pack('<8I',*offsets)+b''.join(sections))
        self.assertEqual(read_resource(pbp,'icon'),PNG)
        self.assertEqual(read_resource(pbp,'overlay'),PNG)
        self.assertEqual(read_resource(pbp,'background'),PNG)
        self.assertEqual(read_resource(pbp,'sound'),sound_fixture())

    def test_at3_must_be_bounded_compatible_riff(self):
        sound=sound_fixture()
        self.assertTrue(valid_at3(sound))
        self.assertFalse(valid_at3(sound.replace(b'WAVE',b'RAVE')))
        self.assertFalse(valid_at3(sound.replace(b'\x70\x02',b'\x01\x00')))
        self.assertFalse(valid_at3(sound[:20]))
        self.assertIsNone(convert_at3_to_mp3(b'anything',ffmpeg='ffmpeg'))
        self.assertIsNone(convert_at3_to_mp3(sound,ffmpeg='not-existing-ffmpeg.exe'))

    def test_cache_immutable_media_fingerprint_and_identity(self):
        g={"id":"f"*24,"relative":"ISO/x.iso","format":"ISO",
           "size":65536,"modified_ns":12345678,"signature":"0"*64}
        k=media_key(volume='SONY-001',game=g)
        self.assertNotEqual(k,media_key(volume='SONY-002',game=g))
        c=XMBAssetCache(self.root/'cache')
        self.assertIsNone(c.cached(k,'background'))
        iso=self.root/'game.iso'
        write_fixture_iso(iso)
        self.assertEqual(c.ensure(k,'icon',iso),PNG)
        self.assertEqual(c.cached(k,'icon'),PNG)
        with patch('psp_xmb_media.read_resource',side_effect=AssertionError('no duplicate USB reads')):
            self.assertEqual(c.ensure(k,'icon',iso),PNG)
        self.assertIsNone(c.cached(media_key(volume='SONY-002',game=g),'icon'))
        with self.assertRaises(ValueError):
            c.path('../outside','background')
        with self.assertRaises(ValueError):
            c.path(k,'exe')

    def test_corrupt_media_or_invalid_kind_rejected(self):
        iso=self.root/'broken.iso'
        iso.write_bytes(b'BROKEN')
        self.assertIsNone(read_resource(iso,'background'))
        self.assertIsNone(read_resource(iso,'sound'))
        with self.assertRaises(ValueError):
            read_resource(iso,'../../path')


if __name__=='__main__':
    unittest.main()
